from collections.abc import Iterable, Mapping
import os
import re
import copy
import json
import logging
import argparse
import tempfile

from src import bdmvinfo, bdmvkey, command, config, environment

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

def get_config_display_name(output_config: config.OutputConfig):
    name = output_config.name
    if output_config.year is not None:
        name = "%s (%i)" % (name, output_config.year)
    if output_config.season is not None:
        name = "%s S%02i" % (name, output_config.season)
        if output_config.episode is not None:
            name = "%sE%02i" % (name, output_config.episode)
            if output_config.episode_title:
                name = "%s %s" % (name, output_config.episode_title)
    if output_config.extra_title:
        name = "%s - %s" % (name, output_config.extra_title)
    if output_config.version:
        name = "%s - %s" % (name, output_config.version)
    return name

def get_config_output_path(output_config: config.OutputConfig):
    subpath = ""
    name = sanitize(output_config.name)
    if output_config.year is not None:
        name = "%s (%i)" % (name, output_config.year)
    filename = name
    if output_config.season is not None:
        subpath = os.path.join(subpath, "Season %02i" % output_config.season)
        filename = "%s S%02i" % (sanitize(output_config.name), output_config.season)
        if output_config.episode is not None:
            filename = "%sE%02i" % (filename, output_config.episode)
            if output_config.episode_title:
                filename = "%s %s" % (filename, sanitize(output_config.episode_title))
    if output_config.extra_title:
        filename = sanitize(output_config.extra_title)
        subpath = os.path.join(subpath, output_config.extra_type)
    if output_config.version:
        filename = "%s - %s" % (filename, sanitize(output_config.version))
    return os.path.join(env.target_directory, *output_config.path, name, subpath, filename + ".mkv")

def map_bdmv_title_streams(title_key: bdmvkey.BdmvTitleKey, stream_type: str, bdmv_streams: Iterable[int], bdmv_derived: Iterable[int], bdmv_to_file: Mapping[int, int]):
    config_to_file: Mapping[config.StreamSourceKey, int] = {}
    actual_bdmv_streams = sorted(i for i in bdmv_streams if i not in bdmv_derived)
    for config_id, bdmv_id in enumerate(actual_bdmv_streams):
        if bdmv_id not in bdmv_to_file: continue
        source_key = config.StreamSourceKey(title=title_key, type=stream_type, index=config_id, derived=False)
        config_to_file[source_key] = bdmv_to_file[bdmv_id]
        derived_id = bdmv_id + 1
        if derived_id in bdmv_to_file and derived_id in bdmv_streams and derived_id in bdmv_derived:
            derived_key = config.StreamSourceKey(title=title_key, type=stream_type, index=config_id, derived=True)
            config_to_file[derived_key] = bdmv_to_file[derived_id]
    return config_to_file

def map_bdmv_title_file(title_key: bdmvkey.BdmvTitleKey, title_info: bdmvinfo.BdmvTitleInfo, title_file: str):
    logging.debug("Streams for %s %s: video=%r audio=%r subtitle=%r derived=%r", title_key.bdmv.name, title_key.title,
        title_info.video_streams, title_info.audio_streams, title_info.subtitle_streams, title_info.derived_streams)
    file_info = json.loads(command.exec_mkvmerge(["-J", title_file], env, verbose))
    bdmv_to_file: Mapping[int, int] = {}
    for track in file_info["tracks"]:
        bdmv_id = int(track["properties"]["number"]) - 1
        file_id = int(track["id"])
        bdmv_to_file[bdmv_id] = file_id
    config_to_file: Mapping[config.StreamSourceKey, int] = {}
    config_to_file.update(map_bdmv_title_streams(title_key, "video", title_info.video_streams, title_info.derived_streams, bdmv_to_file))
    config_to_file.update(map_bdmv_title_streams(title_key, "audio", title_info.audio_streams, title_info.derived_streams, bdmv_to_file))
    config_to_file.update(map_bdmv_title_streams(title_key, "subtitle", title_info.subtitle_streams, title_info.derived_streams, bdmv_to_file))
    return config_to_file

def validate_config_against_bdmvs(output_config: config.OutputConfig, images: Mapping[str, bdmvinfo.BdmvInfo]):
    desired_stream_sources = set([stream.source for stream in output_config.all_streams()])
    for stream_source in desired_stream_sources:
        bdmv = images.get(stream_source.title.bdmv.identifier())
        if not bdmv:
            logging.critical("Expected to have found %s", stream_source.title.bdmv.name)
            exit(1)
        title = bdmv.titles.get(stream_source.title.title)
        if not title:
            logging.critical("Expected %s to have title %s", bdmv.name, stream_source.title.title)
            exit(1)
        streams = { "video": title.video_streams, "audio": title.audio_streams, "subtitle": title.subtitle_streams }[stream_source.type]
        actual_streams = sorted([i for i in streams if i not in title.derived_streams])
        if stream_source.index >= len(actual_streams):
            logging.critical("Expected %s %s to have %s stream %i, but there are only %i",
                bdmv.name, stream_source.title.title, stream_source.type, stream_source.index, len(actual_streams))
            exit(1)
        if stream_source.derived:
            derived_stream_index = actual_streams[stream_source.index] + 1
            if derived_stream_index not in streams or derived_stream_index not in title.derived_streams:
                logging.critical("Expected %s %s %s track %i to have a derived option",
                    bdmv.name, stream_source.title.title, stream_source.type, stream_source.index)
                exit(1)

def validate_config_against_file_mapping(output_config: config.OutputConfig, title: bdmvkey.BdmvTitleKey, track_mapping: Mapping[config.StreamSourceKey, int]):
    desired_streams = set([stream.source for stream in output_config.all_streams() if stream.source.title == title])
    for stream in desired_streams:
        if stream not in track_mapping:
            logging.critical("Expected %s %s to have %s %s %i", stream.title.bdmv.name, stream.title.title,
                                stream.type, "derived track" if stream.derived else "track", stream.index)
            exit(1)
        if not stream.derived:
            derived_stream = config.StreamSourceKey(stream.title, stream.type, stream.index, True)
            if derived_stream in track_mapping and derived_stream not in desired_streams:
                logging.warning("Derived %s track %i is not used in %s %s", derived_stream.type, derived_stream.index,
                                derived_stream.title.bdmv.name, derived_stream.title.title)

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

def process_config(output_config: config.OutputConfig, images: Mapping[str, bdmvinfo.BdmvInfo]):
    display_name = get_config_display_name(output_config)
    logging.info("Processing %s", display_name)
    validate_config_against_bdmvs(output_config, images)
    args = ["--title", display_name]
    with tempfile.TemporaryDirectory(prefix="ExtractMKV", suffix=sanitize(display_name), dir=env.temp_directory) as working_directory:
        track_order = list[str]()
        source_titles = set([stream_config.source.title for stream_config in output_config.all_streams()])
        for source_index, source_title in enumerate(source_titles):
            bdmv = images[source_title.bdmv.identifier()]
            bdmv_title = bdmv.titles[source_title.title]
            title_file = extract_bdmv_title(bdmv, source_title.title, working_directory)
            track_mapping = map_bdmv_title_file(source_title, bdmv_title, title_file)
            validate_config_against_file_mapping(output_config, source_title, track_mapping)
            title_video = [track_mapping[stream.source] for stream in output_config.video_streams if stream.source.title == source_title]
            args += ["--video-tracks", ",".join([str(track) for track in title_video])] if title_video else ["--no-video"]
            title_audio = [track_mapping[stream.source] for stream in output_config.audio_streams if stream.source.title == source_title]
            args += ["--audio-tracks", ",".join([str(track) for track in title_audio])] if title_audio else ["--no-audio"]
            title_subtitle = [track_mapping[stream.source] for stream in output_config.subtitle_streams if stream.source.title == source_title]
            args += ["--subtitle-tracks", ",".join([str(track) for track in title_subtitle])] if title_subtitle else ["--no-subtitles"]
            for stream_config in output_config.all_streams():
                if stream_config.source.title != source_title: continue
                track = track_mapping[stream_config.source]
                track_order.append("%i:%i" % (source_index, track))
                if stream_config.name: args += ["--track-name", str(track) + ":" + stream_config.name]
                if stream_config.language: args += ["--language", str(track) + ":" + stream_config.language]
                if stream_config.default is not None: args += ["--default-track-flag", str(track) + ":" + ("1" if stream_config.default else "0")]
                if stream_config.forced is not None: args += ["--forced-display-flag", str(track) + ":" + ("1" if stream_config.forced else "0")]
                if stream_config.commentary is not None: args += ["--commentary-flag", str(track) + ":" + ("1" if stream_config.commentary else "0")]
                if stream_config.cropping:
                    crop = stream_config.cropping
                    args += ["--cropping", "%s:%i,%i,%i,%i" % (track, crop.left, crop.top, crop.right, crop.bottom)]
            args.append(title_file)
        args += ["--track-order", ",".join(track_order)]
        output_file = get_config_output_path(output_config)
        logging.info("Remuxing to %s", output_file)
        logging.debug("Remux args: %s", " ".join(args))
        command.exec_mkvmerge(["-o", output_file, *args], env, verbose)
    logging.info("Completed %s", display_name)

def get_all_config_source_bdmvs(output_config: config.OutputConfig):
    return set([stream.source.title.bdmv for stream in output_config.all_streams()])

outputs = list[config.OutputConfig]()
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
                title_key = bdmvkey.BdmvTitleKey(source_key, title)
                output_config = config.parse_output_config({ **defaults, **cfg_defaults, **title_config }, title_key)
                if not force:
                    output_path = get_config_output_path(output_config)
                    if os.path.isfile(output_path):
                        logging.debug("%s is already present", get_config_display_name(output_config))
                        continue
                outputs.append(output_config)
if not outputs:
    logging.warning("Did not find any %s matching the selection", "config" if force else "unexported config")
else:
    logging.info("Identified %i titles to export: %s", len(outputs), ", ".join([get_config_display_name(output) for output in outputs]))

required_images = set[str]()
for output_config in outputs:
    required_images.update([source.identifier() for source in get_all_config_source_bdmvs(output_config)])
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
                for output_config in outputs[:]:
                    output_requirements = set([key.identifier() for key in get_all_config_source_bdmvs(output_config)])
                    if not output_requirements <= found_images.keys(): continue
                    process_config(output_config, found_images)
                    outputs.remove(output_config)
            elif entry.is_dir() and not os.path.exists(os.path.join(entry.path, "BDMV")):
                path_queue.append(entry.path)

missing_images = set[bdmvkey.BdmvKey]()
for output_config in outputs:
    output_sources = get_all_config_source_bdmvs(output_config)
    missing_images.update([source for source in output_sources if source.identifier() not in found_images])
if missing_images:
    logging.info("Did not find %s", ", ".join([image.name for image in missing_images]))
