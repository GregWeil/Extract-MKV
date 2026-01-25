from collections.abc import Iterable, Mapping
import os
import re
import copy
import json
import logging
import argparse
import tempfile

from src import bdmvinfo, bdmvkey, command, environment

env_dir = os.path.dirname(os.path.abspath(__file__))
env = environment.get_environment_config(env_dir)

argparser = argparse.ArgumentParser(description="MKV Extractor")
argparser.add_argument("selection", help="Comma separated configuration keys, or ALL to export everything")
argparser.add_argument("--force", action="store_true", help="Extract an mkv even if the destination file already exists")
argparser.add_argument("--verbose", action="store_true", help="Show extra output, including from sub commands")
arguments = argparser.parse_args()
selection = arguments.selection.split(",")
force = arguments.force
verbose = arguments.verbose
logging.basicConfig(level=(logging.DEBUG if verbose else logging.INFO), format="%(asctime)s %(levelname)s %(message)s")

def sanitize(value):
    value = re.sub(r'(^|[\s\.\\/])"([^\s"](?:[^"]*[^\s"])?)"([\s\.\\/\:]|$)', r'\1“\2”\3', value).replace('"','＂')
    value = value.replace('?', '？').replace(':', '꞉').replace('*', '✳').replace('|', '⏐')
    return value.replace('<', '＜').replace('>', '＞').replace('/','⧸').replace('\\', '⧹')

def get_title_display_name(config):
    name = config["name"]
    if "year" in config:
        name = "%s (%i)" % (name, config["year"])
    if "season" in config:
        name = "%s S%02i" % (name, config["season"])
        if "episode" in config:
            name = "%sE%02i" % (name, config["episode"])
    if "title" in config:
        name = "%s %s" % (name, config["title"])
    if "extra" in config:
        name = "%s - %s" % (name, config["extra"])
    if "version" in config:
        name = "%s - %s" % (name, config["version"])
    return name

def get_title_output_path(config):
    path = os.path.join(*config["path"]) if isinstance(config.get("path"), list) else config.get("path", "")
    subpath = ""
    name = sanitize("%s (%i)" % (config["name"], config["year"]) if "year" in config else config["name"])
    filename = name
    if "season" in config:
        subpath = os.path.join(subpath, "Season %02i" % config["season"])
        filename = "%s S%02i" % (config["name"], config["season"])
        if "episode" in config:
            filename = "%sE%02i" % (filename, config["episode"])
    if "title" in config:
        filename = "%s %s" % (filename, config["title"])
    if "extra" in config:
        filename = config["extra"]
        subpath = os.path.join(subpath, config.get("type", "extras"))
    if "version" in config:
        filename = "%s - %s" % (filename, config["version"])
    return os.path.join(env.target_directory, path, name, subpath, sanitize(filename) + ".mkv")

def title_key(track):
    return bdmvkey.BdmvTitleKey(track["_source"], track["_title"])

def normalize_bdmv_title_streams(stream_type: str, config_streams, source_title: bdmvkey.BdmvTitleKey,
                                 bdmv_streams: Iterable[int], bdmv_all_derived: Iterable[int], track_mapping: Mapping[int, int]):
    bdmv_derived = [i for i in bdmv_all_derived if i in bdmv_streams]
    actual_bdmv_streams = sorted(i for i in bdmv_streams if i not in bdmv_derived)
    default_specified = any(config.get("default", False) for config in config_streams)
    used_bdmv_streams = []
    for config in config_streams:
        if title_key(config) != source_title: continue
        track_index = int(config["track"]["index"])
        if track_index >= len(actual_bdmv_streams):
            logging.critical("Failed to normalize %r: Could not find %s track %i, only %i tracks found", config, stream_type, track_index, len(actual_bdmv_streams))
            exit(1)
        actual_index = actual_bdmv_streams[track_index]
        if config["track"].get("core", False) or config["track"].get("forced", False):
            actual_index += 1
            if actual_index not in bdmv_derived:
                logging.critical("Failed to normalize %r: Expected stream %i to be derived for %s track %i", config, actual_index, stream_type, track_index)
                exit(1)
        if actual_index not in track_mapping:
            logging.critical("%s track %r not not found in the extracted mkv", stream_type.title(), config["track"]["index"])
            exit(1)
        if default_specified: config.setdefault("default", False)
        config["_track"] = track_mapping[actual_index]
        used_bdmv_streams.append(actual_index)
    for derived_i in bdmv_derived:
        if stream_type == "audio": continue
        if derived_i not in track_mapping: continue
        if derived_i in used_bdmv_streams: continue
        actual_i = max(i for i in actual_bdmv_streams if i <= derived_i)
        if actual_i not in used_bdmv_streams: continue
        logging.warning("%s track %i has an unused derived track", stream_type.title(), actual_bdmv_streams.index(actual_i))

def normalize_bdmv_title(config, source_title: bdmvkey.BdmvTitleKey, bdmv: bdmvinfo.BdmvInfo, title_file: str):
    bdmv_title = bdmv.titles[source_title.title]
    logging.debug("Streams for %s %s: video=%r audio=%r subtitle=%r derived=%r", bdmv.name, source_title.title,
        bdmv_title.video_streams, bdmv_title.audio_streams, bdmv_title.subtitle_streams, bdmv_title.derived_streams)
    info = json.loads(command.exec_mkvmerge(["-J", title_file], env, verbose))
    track_mapping: Mapping[int, int] = {}
    for track in info["tracks"]:
        track_mapping[track["properties"]["number"] - 1] = track["id"]
    logging.debug("Extracted track mapping: %r", track_mapping)
    normalize_bdmv_title_streams("video", config["video"], source_title, bdmv_title.video_streams, bdmv_title.derived_streams, track_mapping)
    normalize_bdmv_title_streams("audio", config["audio"], source_title, bdmv_title.audio_streams, bdmv_title.derived_streams, track_mapping)
    normalize_bdmv_title_streams("subtitle", config["subtitle"], source_title, bdmv_title.subtitle_streams, bdmv_title.derived_streams, track_mapping)

def extract_bdmv_title(bdmv: bdmvinfo.BdmvInfo, title: str, output_directory: str):
    bdmv_title = bdmv.titles.get(title)
    if not bdmv_title:
        logging.critical("Did not find title %s in %s", title, bdmv.name)
        exit(1)
    logging.info("Extracting %s %s", bdmv.name, title)
    command.exec_makemkv(["mkv", "file:" + bdmv.path, bdmv_title.title_id, output_directory], env, verbose)
    output_file = os.path.join(output_directory, bdmv_title.output_file)
    if not os.path.isfile(output_file):
        logging.critical("Expected file %s to have been created", output_file)
        exit(1)
    return output_file

def process_config(config, images: Mapping[str, bdmvinfo.BdmvInfo]):
    display_name = get_title_display_name(config)
    logging.info("Processing %s", display_name)
    args = ["--title", display_name]
    all_tracks = [*config["video"], *config["audio"], *config["subtitle"]]
    for track in all_tracks:
        bdmv = images.get(track["_source"].identifier())
        if not bdmv:
            logging.critical("Expected to have found %s", track["_source"].name)
            exit(1)
        if track["_title"] not in bdmv.titles:
            logging.critical("Expected %s to have title %s", bdmv.name, track["_title"])
            exit(1)
    with tempfile.TemporaryDirectory(prefix="ExtractMKV", suffix=sanitize(display_name), dir=env.temp_directory) as working_directory:
        source_titles = list(set([title_key(track) for track in all_tracks]))
        for source_title in source_titles:
            bdmv = images[source_title.bdmv.identifier()]
            title_file = extract_bdmv_title(bdmv, source_title.title, working_directory)
            normalize_bdmv_title(config, source_title, bdmv, title_file)
            title_video = [track for track in config["video"] if title_key(track) == source_title]
            args += ["--video-tracks", ",".join([str(track["_track"]) for track in title_video])] if title_video else ["--no-video"]
            title_audio = [track for track in config["audio"] if title_key(track) == source_title]
            args += ["--audio-tracks", ",".join([str(track["_track"]) for track in title_audio])] if title_audio else ["--no-audio"]
            title_subtitle = [track for track in config["subtitle"] if title_key(track) == source_title]
            args += ["--subtitle-tracks", ",".join([str(track["_track"]) for track in title_subtitle])] if title_subtitle else ["--no-subtitles"]
            for track in all_tracks:
                if title_key(track) != source_title: continue
                if "name" in track: args += ["--track-name", str(track["_track"]) + ":" + track["name"]]
                if "language" in track: args += ["--language", str(track["_track"]) + ":" + track["language"]]
                if "default" in track: args += ["--default-track-flag", str(track["_track"]) + ":" + ("1" if track["default"] else "0")]
                if "forced" in track: args += ["--forced-display-flag", str(track["_track"]) + ":" + ("1" if track["forced"] else "0")]
                if "commentary" in track: args += ["--commentary-flag", str(track["_track"]) + ":" + ("1" if track["commentary"] else "0")]
                if "cropping" in track:
                    left = str(track["cropping"].get("left", 0))
                    top = str(track["cropping"].get("top", 0))
                    right = str(track["cropping"].get("right", 0))
                    bottom = str(track["cropping"].get("bottom", 0))
                    args += ["--cropping", str(track["_track"]) + ":" + left + "," + top + "," + right + "," + bottom]
            args.append(title_file)
        args += ["--track-order", ",".join([str(source_titles.index(title_key(track))) + ":" + str(track["_track"]) for track in all_tracks])]
        output_file = get_title_output_path(config)
        logging.info("Remuxing to %s", output_file)
        logging.debug("Remux args: %s", " ".join(args))
        command.exec_mkvmerge(["-o", output_file, *args], env, verbose)
    logging.info("Completed %s", display_name)

titles = []
for config_path in env.config_paths:
    with open(config_path, encoding="utf8") as config_file:
        config_json = json.load(config_file)
        defaults = config_json.pop("", {})
        accept_all = "ALL" in selection or os.path.basename(config_path) in selection
        for source, cfg in config_json.items():
            source_key = bdmvkey.parse_bdmv_key(source)
            if source_key.name not in selection and source_key.hash not in selection and not accept_all: continue
            cfg_defaults = cfg.pop("", {})
            for title, title_config in cfg.items():
                title_config = copy.deepcopy({ **defaults, **cfg_defaults, **title_config })
                if not force:
                    title_path = get_title_output_path(title_config)
                    if os.path.isfile(title_path):
                        logging.debug("%s is already present", get_title_display_name(title_config))
                        continue
                title_config["_sources"] = set()
                title_config.setdefault("video", [{ "track": 0 }])
                title_config.setdefault("audio", [{ "track": 0 }])
                title_config.setdefault("subtitle", [])
                for track_config in [*title_config["video"], *title_config["audio"], *title_config["subtitle"]]:
                    if not isinstance(track_config["track"], dict):
                        track_config["track"] = { "index": track_config["track"] }
                    if "source" in track_config["track"]:
                        track_config["_source"] = bdmvkey.parse_bdmv_key(track_config["track"]["source"])
                        track_config["_title"] = track_config["track"]["title"]
                    else:
                        track_config["_source"] = source_key
                        track_config["_title"] = track_config["track"].get("title", title)
                    title_config["_sources"].add(track_config["_source"].identifier())
                titles.append(title_config)
if not titles:
    logging.warning("Did not find any %s matching the selection", "config" if force else "unexported config")
else:
    logging.info("Identified %i titles to export: %s", len(titles), ", ".join([get_title_display_name(title) for title in titles]))

required_images = set().union(*[title["_sources"] for title in titles])
found_images: Mapping[str, bdmvinfo.BdmvInfo] = {}

path_queue = [env.source_directory]
while path_queue:
    path = path_queue.pop()
    try:
        path_iterator = os.scandir(path)
    except OSError as error:
        logging.debug("Failed to scan %s: %s", path, error)
        continue
    with path_iterator:
        for entry in path_iterator:
            bdmv_key = bdmvkey.identify_bdmv_path(entry.name, entry.path)
            if bdmv_key.identifier() in required_images:
                logging.info("Scanning %s", entry.path if verbose else entry.name)
                makemkv_info = command.exec_makemkv(["info", "file:" + entry.path], env, verbose)
                bdmv = bdmvinfo.parse_bdmv_info(entry.name, entry.path, makemkv_info)
                found_images[bdmv_key.identifier()] = bdmv
                for title in titles[:]:
                    if not title["_sources"] <= found_images.keys(): continue
                    process_config(title, found_images)
                    titles.remove(title)
            elif entry.is_dir() and not os.path.exists(os.path.join(entry.path, "BDMV")):
                path_queue.append(entry.path)
if titles:
    missing_images = {}
    for title in titles:
        for track in [*title["video"], *title["audio"], *title["subtitle"]]:
            if track["_source"][-1] in found_images: continue
            missing_images[track["_source"][-1]] = track["_source"][0]
    logging.info("Did not find %s", ", ".join(missing_images.values()))
