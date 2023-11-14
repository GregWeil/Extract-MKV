import os
import json
import logging
import argparse
import tempfile
import subprocess

MAKEMKV_ANGLEINFO = 15
MAKEMKV_SOURCEFILENAME = 16
MAKEMKV_ORIGINALTITLEID = 24
MAKEMKV_OUTPUTFILENAME = 27

env_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(env_dir, "env.json")
with open(env_path) as env_json:
    env = json.load(env_json)
    config_path = os.path.join(env_dir, env["config"])
    source_directory = os.path.join(env_dir, env["source"])
    target_directory = os.path.join(env_dir, env["destination"])
    makemkvcon = env["makemkvcon"] if "makemkvcon" in env else "makemkvcon"
    mkvmerge = env["mkvmerge"] if "mkvmerge" in env else "mkvmerge"

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

def extract_bdmv_title(name, config, directory, title, title_output):
    logging.info("Extracting " + config["name"])
    with tempfile.TemporaryDirectory(prefix=name, suffix=title) as working_directory:
        working_file = os.path.join(working_directory, title_output)
        target_file = os.path.join(target_directory, config["name"] + ".mkv")
        args = []
        args += ["--audio-tracks", ",".join([str(track["track"]) for track in config["audio"]])]
        args += ["--subtitle-tracks", ",".join([str(track["track"]) for track in config["subtitle"]])]
        all_tracks = [{ "track": 0 }, *config["audio"], *config["subtitle"]]
        args += ["--track-order", ",".join(["0:" + str(track["track"]) for track in all_tracks])]
        for track in all_tracks:
            if "name" in track: args += ["--track-name", str(track["track"]) + ":" + track["name"]]
            if "language" in track: args += ["--language", str(track["track"]) + ":" + track["language"]]
            if "default" in track: args += ["--default-track-flag", str(track["track"]) + ":" + ("1" if track["default"] else "0")]
            if "forced" in track: args += ["--forced-display-flag", str(track["track"]) + ":" + ("1" if track["forced"] else "0")]
            if "commentary" in track: args += ["--commentary-flag", str(track["track"]) + ":" + ("1" if track["commentary"] else "0")]
        logging.debug("Remux args: " + " ".join(args))
        subprocess.run([makemkvcon, "mkv", "file:" + directory, title, working_directory], check=True, stdout=(None if arguments.verbose else subprocess.DEVNULL))
        if not os.path.isfile(working_file):
            logging.critical("Expected file " + working_file + " to have been created")
            exit(1)
        logging.info("Remuxing " + config["name"])
        subprocess.run([mkvmerge, "-o", target_file, *args, working_file], check=True, stdout=(None if arguments.verbose else subprocess.DEVNULL))
        logging.info("Completed " + config["name"] + " at " + target_file)

def extract_bdmv(name, config, directory):
    logging.info("Processing " + name)
    result = subprocess.run([makemkvcon, "--robot", "info", directory], check=True, stdout=subprocess.PIPE, universal_newlines=True)
    title_file = {}
    title_angle = {}
    title_output = {}
    for line in result.stdout.splitlines():
        if not line.startswith("TINFO:"): continue
        [title, field, code, value] = line[6:].split(",", 3)
        if int(field) == MAKEMKV_ANGLEINFO:
            title_angle[title] = value.strip('"')
        if int(field) == MAKEMKV_SOURCEFILENAME:
            title_file[title] = value.strip('"')
        if int(field) == MAKEMKV_OUTPUTFILENAME:
            title_output[title] = value.strip('"')
    source_title = {}
    for title in title_file:
        if not title in title_angle: source_title[title_file[title]] = title
        else: source_title[title_file[title] + ":" + title_angle[title]] = title
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
        extract_bdmv_title(name, config[source], directory, title, output)

with open(config_path) as config_file:
    config = json.load(config_file)
    for dir in os.listdir(source_directory):
        if not dir in config: continue
        if not dir in selection and not "ALL" in selection: continue
        extract_bdmv(dir, config[dir], os.path.join(source_directory, dir))
