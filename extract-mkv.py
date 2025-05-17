import os
import re
import copy
import json
import glob
import logging
import argparse
import tempfile
import subprocess
import hashlib

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

def sanitize(value):
    value = re.sub(r'(^|[\s\.\\/])"([^\s"](?:[^"]*[^\s"])?)"([\s\.\\/]|$)', r'\1“\2”\3', value).replace('"','＂')
    value = value.replace('?', '？').replace(':', '꞉').replace('*', '✳').replace('|', '⏐')
    return value.replace('<', '＜').replace('>', '＞').replace('/','⧸').replace('\\', '⧹')

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

def source_key(track):
    return (tuple(track["_source"]), track["_title"])

def normalize_bdmv_title_streams(stream_type, config_streams, source_title, bdmv, track_mapping):
    title_id = bdmv["titles"][source_title[1]]
    bdmv_streams = bdmv[stream_type].get(title_id, [])
    bdmv_derived = [i for i in bdmv["derived"].get(title_id, []) if i in bdmv_streams]
    actual_bdmv_streams = sorted(i for i in bdmv_streams if i not in bdmv_derived)
    default_specified = any(config.get("default", False) for config in config_streams)
    used_bdmv_streams = []
    for config in config_streams:
        if source_key(config) != source_title: continue
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
            logging.critical("%s track %r not not found in the extracted mkv", stream_type.title(), config_track)
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

def normalize_bdmv_title(config, source_title, bdmv, title_file):
    title_id = bdmv["titles"][source_title[1]]
    logging.debug("Streams for %s %s: video=%r audio=%r subtitle=%r derived=%r", bdmv["name"], source_title[1],
        bdmv["video"].get(title_id, []), bdmv["audio"].get(title_id, []), bdmv["subtitle"].get(title_id, []), bdmv["derived"].get(title_id, []))
    info = json.loads(exec([*mkvmerge, "-J", title_file]))
    track_mapping = {}
    for track in info["tracks"]:
        track_mapping[track["properties"]["number"] - 1] = track["id"]
    logging.debug("Extracted track mapping: %r", track_mapping)
    normalize_bdmv_title_streams("video", config["video"], source_title, bdmv, track_mapping)
    normalize_bdmv_title_streams("audio", config["audio"], source_title, bdmv, track_mapping)
    normalize_bdmv_title_streams("subtitle", config["subtitle"], source_title, bdmv, track_mapping)

def extract_bdmv_title(bdmv, title, output_directory):
    title_id = bdmv["titles"][title]
    if not title_id:
        logging.critical("Did not find title %s in %s", title, bdmv["name"])
        exit(1)
    title_output = bdmv["output"][title_id]
    if not title_output:
        logging.critical("Did not get an output file for %s %s", bdmv["name"], title)
        exit(1)
    logging.info("Extracting %s %s", bdmv["name"], title)
    exec([*makemkvcon, *MAKEMKV_STANDARD_ARGS, "mkv", "file:" + bdmv["path"], title_id, output_directory], parse_makemkv_progress)
    output_file = os.path.join(output_directory, title_output)
    if not os.path.isfile(output_file):
        logging.critical("Expected file %s to have been created", output_file)
        exit(1)
    return output_file

def scan_bdmv(name, path):
    logging.info("Scanning %s", path if verbose else name)
    result = exec([*makemkvcon, *MAKEMKV_STANDARD_ARGS, "info", "file:" + path], parse_makemkv_progress)
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
        if title not in title_angle: source_title[title_file[title]] = title
        else: source_title[title_file[title] + ":" + title_angle[title]] = title
    for title in title_originalid:
        source_title[title_originalid[title]] = title
    for title in title_comment:
        source_title[title_comment[title]] = title
    logging.debug("Identified titles: %s", json.dumps(source_title))
    return {
        "name": name,
        "path": path,
        "titles": source_title,
        "output": title_output,
        "bytes": title_bytes,
        "video": stream_video,
        "audio": stream_audio,
        "subtitle": stream_subtitle,
        "derived": stream_derived,
    }

def process_config(config, images):
    display_name = get_title_display_name(config)
    logging.info("Processing %s", display_name)
    with tempfile.TemporaryDirectory(prefix="ExtractMKV", suffix=sanitize(display_name), dir=temp_directory) as working_directory:
        all_tracks = [*config["video"], *config["audio"], *config["subtitle"]]
        source_titles = list(dict.fromkeys([source_key(track) for track in all_tracks]))
        args = ["--title", display_name]
        for source_title in source_titles:
            bdmv = images[source_title[0][-1]]
            if not bdmv:
                logging.critical("Expected to have found %s", source_title[0][0])
                exit(1)
            title_file = extract_bdmv_title(bdmv, source_title[1], working_directory)
            normalize_bdmv_title(config, source_title, bdmv, title_file)
            title_video = [track for track in config["video"] if source_key(track) == source_title]
            args += ["--video-tracks", ",".join([str(track["_track"]) for track in title_video])] if title_video else ["--no-video"]
            title_audio = [track for track in config["audio"] if source_key(track) == source_title]
            args += ["--audio-tracks", ",".join([str(track["_track"]) for track in title_audio])] if title_audio else ["--no-audio"]
            title_subtitle = [track for track in config["subtitle"] if source_key(track) == source_title]
            args += ["--subtitle-tracks", ",".join([str(track["_track"]) for track in title_subtitle])] if title_subtitle else ["--no-subtitles"]
            for track in all_tracks:
                if source_key(track) != source_title: continue
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
        args += ["--track-order", ",".join([str(source_titles.index(source_key(track))) + ":" + str(track["_track"]) for track in all_tracks])]
        output_file = get_title_output_path(config)
        logging.info("Remuxing to %s", output_file)
        logging.debug("Remux args: %s", " ".join(args))
        exec([*mkvmerge, "-o", output_file, *args], parse_mkvmerge_progress)
        logging.info("Completed %s", display_name)

titles = []
for config_path in config_paths:
    with open(config_path, encoding="utf8") as config_file:
        config_json = json.load(config_file)
        defaults = config_json.pop("", {})
        accept_all = "ALL" in selection or os.path.basename(config_path) in selection
        for source, cfg in config_json.items():
            sources = [s.strip() for s in source.split(":")]
            if not set(sources) & set(selection) and not accept_all: continue
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
                        track_config["_source"] = [s.strip() for s in track_config["track"]["source"].split(":")]
                        track_config["_title"] = track_config["track"]["title"]
                    else:
                        track_config["_source"] = sources
                        track_config["_title"] = track_config["track"].get("title", title)
                    title_config["_sources"].add(track_config["_source"][-1])
                titles.append(title_config)
if not titles:
    logging.warning("Did not find any %s matching the selection", "config" if force else "unexported config")
else:
    logging.info("Identified %i titles to export: %s", len(titles), ", ".join([get_title_display_name(title) for title in titles]))

required_images = set().union(*[title["_sources"] for title in titles])
found_images = {}

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
            unit_key_hash = None
            unit_key_path = os.path.join(entry.path, "MAKEMKV", "AACS", "Unit_Key_RO.inf")
            if os.path.exists(unit_key_path):
                with open(unit_key_path, "rb") as unit_key_file:
                    unit_key_hash = hashlib.file_digest(unit_key_file, "sha1").hexdigest()
            if unit_key_hash in required_images or entry.name in required_images:
                bdmv = scan_bdmv(entry.name, entry.path)
                if unit_key_hash: found_images[unit_key_hash] = bdmv
                found_images[entry.name] = bdmv
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
