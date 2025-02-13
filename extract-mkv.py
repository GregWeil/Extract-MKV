import os
import re
import copy
import json
import glob
import logging
import argparse
import tempfile
import subprocess

MAKEMKV_ANGLEINFO = 15
MAKEMKV_SOURCEFILENAME = 16
MAKEMKV_ORIGINALTITLEID = 24
MAKEMKV_OUTPUTFILENAME = 27
MAKEMKV_OUTPUTSIZEBYTES = 11
MAKEMKV_COMMENT = 49

MAKEMKV_TYPE = 1
MAKEMKV_TYPE_VIDEO = 6201
MAKEMKV_TYPE_AUDIO = 6202
MAKEMKV_TYPE_SUBTITLE = 6203

MAKEMKV_STREAMFLAGS = 22
MAKEMKV_STREAMFLAGS_DERIVED = 2048

env_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(env_dir, "env.json")
with open(env_path) as env_json:
    env = json.load(env_json)
    configs = env["config"] if isinstance(env["config"], list) else [env["config"]]
    config_paths = sum([glob.glob(config, root_dir=env_dir) for config in configs], [])
    source_directory = os.path.join(env_dir, env["source"])
    target_directory = os.path.join(env_dir, env["destination"])
    temp_directory = os.path.join(env_dir, env["temp"]) if "temp" in env else None
    makemkvcon = env["makemkvcon"] if "makemkvcon" in env else "makemkvcon"
    makemkvcon = makemkvcon if isinstance(makemkvcon, list) else [makemkvcon]
    mkvmerge = env["mkvmerge"] if "mkvmerge" in env else "mkvmerge"
    mkvmerge = mkvmerge if isinstance(mkvmerge, list) else [mkvmerge]

if makemkvcon[0].startswith(("/", "./", "../")):
    makemkvcon[0] = os.path.join(env_dir, makemkvcon[0])
    if not os.path.isfile(makemkvcon[0]):
        logging.error("Could not find makemkvcon")
        exit(1)
if mkvmerge[0].startswith(("/", "./", "../")):
    mkvmerge[0] = os.path.join(env_dir, mkvmerge[0])
    if not os.path.isfile(mkvmerge[0]):
        logging.error("Could not find mkvmerge")
        exit(1)

argparser = argparse.ArgumentParser(description="MKV Extractor")
argparser.add_argument("selection", help="Comma separated configuration keys, or ALL to export everything")
argparser.add_argument("--force", action="store_true", help="Extract an mkv even if the destination file already exists")
argparser.add_argument("--verbose", action="store_true", help="Show extra output, including from sub commands")
arguments = argparser.parse_args()
selection = arguments.selection.split(",")
force = arguments.force
verbose = arguments.verbose
logging.basicConfig(level=(logging.DEBUG if verbose else logging.INFO), format="%(asctime)s %(levelname)s %(message)s")

MAKEMKV_STANDARD_ARGS = ["--robot", "--noscan", "--minlength=0", "--messages=-stdout", "--debug=-null", "--progress=-null" if verbose else "--progress=-stdout"]

def exec(args, parse_progress=None):
    logging.debug(' '.join(args))
    progress_total = 40
    progress_segments = -1
    output = ""
    with subprocess.Popen(args, stdout=subprocess.PIPE, text=True, universal_newlines=True, encoding="UTF-8") as process:
        for line in process.stdout:
            if verbose: print(line, end="")
            elif parse_progress:
                progress = parse_progress(line)
                if progress != None:
                    segments = int(progress * progress_total)
                    if segments != progress_segments:
                        print("\r[" + ("#" * segments) + ("-" * (progress_total - segments)) + "]", end="")
                    progress_segments = segments
            output += line
    if process.returncode != 0:
        if not verbose:
            if parse_progress: print("")
            print(output, end="")
        raise subprocess.CalledProcessError(process.returncode, process.args)
    elif parse_progress and not verbose: print("\r  " + (" " * progress_total), end="\r")
    return output

def parse_makemkv_progress(line):
    if not line.startswith("PRGV:"): return None
    [current, total, max] = line[5:].split(",")
    return int(current) / int(max)

def parse_mkvmerge_progress(line):
    if not line.startswith("Progress:"): return None
    return int(line[10:-2]) / 100

def get_title_display_name(config):
    name = config["name"]
    if "year" in config:
        name = "%s (%i)" % (config["name"], config["year"])
    if "season" in config:
        name = "%s S%02i" % (name, config["season"])
        if "episode" in config:
            name = "%sE%02i" % (name, config["episode"])
    if "version" in config:
        name = "%s - %s" % (name, config["version"])
    if "extra" in config:
        name = "%s - %s" % (name, config["extra"])
    return name

def normalize_config_streams(display_name, stream_type, config_streams, all_streams, derived_streams):
    actual_streams = sorted(i for i in all_streams if i not in derived_streams)
    default_specified = any(config.get("default", False) for config in config_streams)
    for config in config_streams:
        config["_type"] = stream_type
        config_track = config["track"] if isinstance(config["track"], dict) else { "index": config["track"] }
        track_index = int(config_track["index"])
        track_derived = config_track.get("core", False) or config_track.get("forced", False)
        if track_index >= len(actual_streams):
            logging.critical("Failed to normalize %s: Could not find %s track %i, only %i tracks found", display_name, stream_type, track_index, len(actual_streams))
            exit(1)
        actual_index = actual_streams[track_index]
        if track_derived:
            actual_index += 1
            if actual_index not in all_streams or actual_index not in derived_streams:
                logging.critical("Failed to normalize %s: Expected stream %i to be derived for %s track %r", display_name, actual_index, stream_type, config["track"])
                exit(1)
        if default_specified: config.setdefault("default", False)
        config["_track"] = actual_index
        config["_potential_derived"] = []
    used_streams = [config["_track"] for config in config_streams]
    for derived_i in derived_streams:
        if not derived_i in all_streams: continue
        if derived_i in used_streams: continue
        actual_i = max(i for i in actual_streams if i <= derived_i)
        for config in config_streams:
            if config["_track"] == actual_i:
                config["_potential_derived"].append(derived_i)

def normalize_config_source(config, video_streams, audio_streams, subtitle_streams, derived_streams):
    display_name = get_title_display_name(config)
    normalize_config_streams(display_name, "video", config.setdefault("video", [{ "track": 0 }]), video_streams, derived_streams)
    normalize_config_streams(display_name, "audio", config.setdefault("audio", [{ "track": 0 }]), audio_streams, derived_streams)
    normalize_config_streams(display_name, "subtitle", config.setdefault("subtitle", []), subtitle_streams, derived_streams)

def sanitize(value):
    value = re.sub(r'(^|[\s\.\\/])"([^\s\.\\/])', r'\1“\2', value)
    value = re.sub(r'([^\s\.\\/])"([\s\.\\/]|$)', r'\1”\2', value)
    return value.replace('"','＂').replace("?", "？").replace(":", "꞉").replace('/','⧸').replace('\\', '⧹')

def get_title_output_path(config):
    path = os.path.join(*config["path"]) if isinstance(config.get("path"), list) else config.get("path", "")
    name = sanitize("%s (%i)" % (config["name"], config["year"]) if "year" in config else config["name"])
    subpath = ""
    filename = "%s - %s" % (name, config["version"]) if "version" in config else name
    if "season" in config:
        subpath = os.path.join(subpath, "Season %02i" % config["season"])
        if "episode" in config:
            filename = "%s S%02iE%02i" % (config["name"], config["season"], config["episode"])
    if "extra" in config:
        filename = config["extra"]
        subpath = os.path.join(subpath, config.get("type", "extras"))
    return os.path.join(target_directory, path, name, subpath, sanitize(filename) + ".mkv")

def extract_bdmv_title(name, config, directory, title, title_output):
    target_file = get_title_output_path(config)
    display_name = get_title_display_name(config)
    with tempfile.TemporaryDirectory(prefix=name, suffix=title, dir=temp_directory) as working_directory:
        working_file = os.path.join(working_directory, title_output)
        all_tracks = [*config["video"], *config["audio"], *config["subtitle"]]

        logging.info("Extracting %s", display_name)
        exec([*makemkvcon, *MAKEMKV_STANDARD_ARGS, "mkv", "file:" + directory, title, working_directory], parse_makemkv_progress)

        if not os.path.isfile(working_file):
            logging.critical("Expected file %s to have been created", working_file)
            exit(1)
        
        info = json.loads(exec([*mkvmerge, "-J", working_file]))
        track_mapping = {}
        for track in info["tracks"]:
            track_mapping[track["properties"]["number"] - 1] = track["id"]
        logging.debug("Extracted track mapping: %r", track_mapping)
        for track in all_tracks:
            if not track["_track"] in track_mapping:
                logging.critical("%s track %r not not found in the extracted mkv", track["_type"], track["track"])
                exit(1)
            track["_track"] = track_mapping[track["_track"]]
            if track["_type"] == "audio": continue
            if any(index in track_mapping for index in track["_potential_derived"]):
                logging.warning("%s track %r has an unused derived track", track["_type"].title(), track["track"])
            
        logging.info("Remuxing to %s", target_file)
        args = ["--title", display_name]
        if config["video"]: args += ["--video-tracks", ",".join([str(track["_track"]) for track in config["video"]])]
        if config["audio"]: args += ["--audio-tracks", ",".join([str(track["_track"]) for track in config["audio"]])]
        if config["subtitle"]: args += ["--subtitle-tracks", ",".join([str(track["_track"]) for track in config["subtitle"]])]
        if all_tracks: args += ["--track-order", ",".join(["0:" + str(track["_track"]) for track in all_tracks])]
        for track in all_tracks:
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
        logging.debug("Remux args: %s", " ".join(args))
        exec([*mkvmerge, "-o", target_file, *args, working_file], parse_mkvmerge_progress)
        logging.info("Completed %s", display_name)

def extract_bdmv(name, config, directory):
    logging.info("Processing %s", name)
    result = exec([*makemkvcon, *MAKEMKV_STANDARD_ARGS, "info", "file:" + directory], parse_makemkv_progress)
    title_file = {}
    title_angle = {}
    title_originalid = {}
    title_comment = {}
    title_output = {}
    title_bytes = {}
    stream_video = {}
    stream_audio = {}
    stream_subtitle = {}
    stream_derived = {}
    for line in result.splitlines():
        if line.startswith("TINFO:"):
            [title, field, code, value] = line[6:].split(",", 3)
            if int(field) == MAKEMKV_SOURCEFILENAME:
                title_file[title] = value.strip('"')
            if int(field) == MAKEMKV_ANGLEINFO:
                title_angle[title] = value.strip('"')
            if int(field) == MAKEMKV_ORIGINALTITLEID:
                title_originalid[title] = value.strip('"')
            if int(field) == MAKEMKV_COMMENT:
                title_comment[title] = value.strip('"')
            if int(field) == MAKEMKV_OUTPUTFILENAME:
                title_output[title] = value.strip('"')
            if int(field) == MAKEMKV_OUTPUTSIZEBYTES:
                title_bytes[title] = int(value.strip('"'))
        if line.startswith("SINFO:"):
            [title, stream, field, code, value] = line[6:].split(",", 4)
            if int(field) == MAKEMKV_TYPE:
                if int(code) == MAKEMKV_TYPE_VIDEO:
                    stream_video.setdefault(title, []).append(int(stream))
                if int(code) == MAKEMKV_TYPE_AUDIO:
                    stream_audio.setdefault(title, []).append(int(stream))
                if int(code) == MAKEMKV_TYPE_SUBTITLE:
                    stream_subtitle.setdefault(title, []).append(int(stream))
            if int(field) == MAKEMKV_STREAMFLAGS:
                if int(value.strip('"')) & MAKEMKV_STREAMFLAGS_DERIVED:
                    stream_derived.setdefault(title, []).append(int(stream))
    source_title = {}
    for title in title_file:
        if not title in title_angle: source_title[title_file[title]] = title
        else: source_title[title_file[title] + ":" + title_angle[title]] = title
    for title in title_originalid:
        source_title[title_originalid[title]] = title
    for title in title_comment:
        source_title[title_comment[title]] = title
    logging.debug("Identified titles: %s", json.dumps(source_title))
    for source in config:
        title = source_title[source]
        if not title:
            logging.critical("Did not get a title for %s %s", name, source)
            exit(1)
        output = title_output[title]
        if not output:
            logging.critical("Did not get an output file for %s %s", name, source)
            exit(1)
        logging.debug("Streams for %s: video=%r audio=%r subtitle=%r derived=%r", source,
            stream_video.get(title, []), stream_audio.get(title, []), stream_subtitle.get(title, []), stream_derived.get(title, []))
        normalize_config_source(config[source], stream_video.get(title, []), stream_audio.get(title, []), stream_subtitle.get(title, []), stream_derived.get(title, []))
        extract_bdmv_title(name, config[source], directory, title, output)

config = {}
title_names = []
for config_path in config_paths:
    with open(config_path, encoding="utf8") as config_file:
        config_json = json.load(config_file)
        defaults = config_json.pop("", {})
        accept_all = "ALL" in selection or os.path.basename(config_path) in selection
        for name, cfg in config_json.items():
            if not name in selection and not accept_all: continue
            cfg_defaults = cfg.pop("", {})
            for title, title_config in cfg.items():
                title_config = { **defaults, **cfg_defaults, **title_config }
                title_name = get_title_display_name(title_config)
                if not force:
                    title_path = get_title_output_path(title_config)
                    if os.path.isfile(title_path):
                        logging.debug("%s is already present", title_name)
                        continue
                config.setdefault(name, {})[title] = copy.deepcopy(title_config)
                title_names.append(title_name)
if not config:
    logging.warning("Did not find any %s matching the selection", "config" if force else "unexported config")
else:
    logging.info("Identified %i titles to export: %s", len(title_names), ", ".join(title_names))
path_queue = [source_directory]
while path_queue:
    path = path_queue.pop()
    try:
        path_iterator = os.scandir(path)
    except OSError as error:
        logging.debug("Failed to scan %s: %s", path, error)
        continue
    with path_iterator:
        for entry in path_iterator:
            if entry.name in config:
                extract_bdmv(entry.name, config.pop(entry.name), entry.path)
            elif entry.is_dir() and not os.path.exists(os.path.join(entry.path, "BDMV")):
                path_queue.append(entry.path)
if config:
    logging.info("Did not find %s", ",".join(config.keys()))
