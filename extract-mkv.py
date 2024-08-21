import os
import re
import copy
import json
import logging
import argparse
import tempfile
import subprocess

MAKEMKV_ANGLEINFO = 15
MAKEMKV_SOURCEFILENAME = 16
MAKEMKV_ORIGINALTITLEID = 24
MAKEMKV_OUTPUTFILENAME = 27

MAKEMKV_TYPE = 1
MAKEMKV_TYPE_VIDEO = 6201
MAKEMKV_TYPE_AUDIO = 6202
MAKEMKV_TYPE_SUBTITLE = 6203

MAKEMKV_STREAMFLAGS = 22
MAKEMKV_STREAMFLAGS_DERIVED = 2048

MAKEMKV_TRACK_REMOVED = re.compile("track #(\\d+?) turned out to be empty and was removed")

env_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(env_dir, "env.json")
with open(env_path) as env_json:
    env = json.load(env_json)
    configs = env["config"] if isinstance(env["config"], list) else [env["config"]]
    config_paths = [os.path.join(env_dir, config) for config in configs]
    source_directory = os.path.join(env_dir, env["source"])
    target_directory = os.path.join(env_dir, env["destination"])
    temp_directory = os.path.join(env_dir, env["temp"]) if "temp" in env else None
    makemkvcon = os.path.join(env_dir, env["makemkvcon"]) if "makemkvcon" in env else "makemkvcon"
    mkvmerge = os.path.join(env_dir, env["mkvmerge"]) if "mkvmerge" in env else "mkvmerge"

if not os.path.isfile(makemkvcon):
    logging.error("Could not find makemkvcon64")
    exit(1)
if not os.path.isfile(mkvmerge):
    logging.error("Could not find mkvmerge")
    exit(1)

argparser = argparse.ArgumentParser(description="MKV Extractor")
argparser.add_argument("selection")
argparser.add_argument("--verbose", action="store_true")
arguments = argparser.parse_args()
selection = arguments.selection.split(",")
logging.basicConfig(level=(logging.DEBUG if arguments.verbose else logging.INFO), format="%(asctime)s %(levelname)s %(message)s")

def exec(args):
    logging.debug(' '.join(args))
    output = ""
    with subprocess.Popen(args, stdout=subprocess.PIPE, text=True, universal_newlines=True) as process:
        for line in process.stdout:
            if arguments.verbose: print(line, end="")
            output += line
    if process.returncode != 0:
        if not arguments.verbose: print(output, end="")
        raise subprocess.CalledProcessError(process.returncode, process.args)
    return output

def normalize_config_streams(config_streams, actual_streams, derived_streams):
    for config in config_streams:
        config_track = config["track"] if isinstance(config["track"], dict) else { "index": config["track"] }
        track_index = int(config_track["index"])
        track_derived = config_track.get("core", False) or config_track.get("forced", False)
        actual_index = sorted([i for i in actual_streams if i not in derived_streams])[track_index]
        if track_derived:
            actual_index += 1
            if actual_index not in actual_streams or actual_index not in derived_streams:
                logging.critical("Expected stream %d to be derived for %r", actual_index, config)
                exit(1)
        config["track"] = actual_index

def normalize_config_source(config, video_streams, audio_streams, subtitle_streams, derived_streams):
    config.setdefault("video", [{ "track": 0 }])
    normalize_config_streams(config.setdefault("video", [{ "track": 0 }]), video_streams, derived_streams)
    normalize_config_streams(config.setdefault("audio", [{ "track": 0 }]), audio_streams, derived_streams)
    normalize_config_streams(config.setdefault("subtitle", []), subtitle_streams, derived_streams)

def get_title_output_path(config):
    path = os.path.join(*config["path"]) if isinstance(config.get("path"), list) else config.get("path", "")
    name = "%s (%i)" % (config["name"], config["year"]) if "year" in config else config["name"]
    subpath = ""
    filename = "%s - %s" % (name, config["version"]) if "version" in config else name
    if "season" in config:
        subpath = "Season %02i" % config["season"]
        if "episode" in config:
            filename = "%s S%02iE%02i" % (config["name"], config["season"], config["episode"])
    return os.path.join(target_directory, path, name, subpath, filename + ".mkv")

def extract_bdmv_title(name, config, directory, title, title_output):
    target_file = get_title_output_path(config)
    logging.info("Extracting %s", os.path.splitext(os.path.basename(target_file))[0])
    with tempfile.TemporaryDirectory(prefix=name, suffix=title, dir=temp_directory) as working_directory:
        working_file = os.path.join(working_directory, title_output)

        result = exec([makemkvcon, "--robot", "--noscan", "mkv", "file:" + directory, title, working_directory])
        if not os.path.isfile(working_file):
            logging.critical("Expected file %s to have been created", working_file)
            exit(1)
        removed_tracks = sorted(set(int(track) for track in MAKEMKV_TRACK_REMOVED.findall(result)))
        logging.debug("The following tracks were removed: %r", removed_tracks)
        all_tracks = [*config["video"], *config["audio"], *config["subtitle"]]
        for track in all_tracks:
            if track["track"] in removed_tracks:
                logging.critical("Desired track %d was removed because it was empty", track)
                exit(1)
            track["track"] -= sum(1 if index < track["track"] else 0 for index in removed_tracks)
            
        logging.info("Remuxing " + config["name"])
        args = []
        args += ["--video-tracks", ",".join([str(track["track"]) for track in config["video"]])]
        args += ["--audio-tracks", ",".join([str(track["track"]) for track in config["audio"]])]
        args += ["--subtitle-tracks", ",".join([str(track["track"]) for track in config["subtitle"]])]
        args += ["--track-order", ",".join(["0:" + str(track["track"]) for track in all_tracks])]
        for track in all_tracks:
            if "name" in track: args += ["--track-name", str(track["track"]) + ":" + track["name"]]
            if "language" in track: args += ["--language", str(track["track"]) + ":" + track["language"]]
            if "default" in track: args += ["--default-track-flag", str(track["track"]) + ":" + ("1" if track["default"] else "0")]
            if "forced" in track: args += ["--forced-display-flag", str(track["track"]) + ":" + ("1" if track["forced"] else "0")]
            if "commentary" in track: args += ["--commentary-flag", str(track["track"]) + ":" + ("1" if track["commentary"] else "0")]
            if "cropping" in track:
                left = str(track["cropping"].get("left", 0))
                top = str(track["cropping"].get("top", 0))
                right = str(track["cropping"].get("right", 0))
                bottom = str(track["cropping"].get("bottom", 0))
                args += ["--cropping", str(track["track"]) + ":" + left + "," + top + "," + right + "," + bottom]
        logging.debug("Remux args: " + " ".join(args))
        exec([mkvmerge, "-o", target_file, *args, working_file])
        logging.info("Completed " + config["name"] + " at " + target_file)

def extract_bdmv(name, config, directory):
    logging.info("Processing " + name)
    result = exec([makemkvcon, "--robot", "--noscan", "info", directory])
    title_file = {}
    title_angle = {}
    title_originalid = {}
    title_output = {}
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
            if int(field) == MAKEMKV_OUTPUTFILENAME:
                title_output[title] = value.strip('"')
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
    logging.debug("Identified titles: " + json.dumps(source_title))
    for source in config:
        title = source_title[source]
        if not title:
            logging.critical("Did not get a title for " + name + " " + source)
            exit(1)
        output = title_output[title]
        if not output:
            logging.critical("Did not get an output file for " + name + " " + source)
            exit(1)
        logging.debug("Streams for %s: video=%r audio=%r subtitle=%r derived=%r", source,
            stream_video.get(title, []), stream_audio.get(title, []), stream_subtitle.get(title, []), stream_derived.get(title, []))
        normalize_config_source(config[source], stream_video.get(title, []), stream_audio.get(title, []), stream_subtitle.get(title, []), stream_derived.get(title, []))
        extract_bdmv_title(name, config[source], directory, title, output)

for config_path in config_paths:
    with open(config_path, encoding="utf8") as config_file:
        config = json.load(config_file)
        defaults = config.pop("", {})
        for path, dirs, files in os.walk(source_directory):
            for name in [*dirs, *files]:
                if not name in config: continue
                if not name in selection and not "ALL" in selection: continue
                if name in dirs and not os.path.exists(os.path.join(path, name, "BDMV")): continue
                for title in config[name]:
                    config[name][title] = dict(copy.deepcopy(defaults), **config[name][title])
                extract_bdmv(name, config[name], os.path.join(path, name))
