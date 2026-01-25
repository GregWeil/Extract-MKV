from collections.abc import Iterable
from dataclasses import dataclass
import logging
import os.path
import glob
import json

@dataclass(kw_only=True)
class EnvironmentConfig:
    makemkvcon: Iterable[str]
    mkvmerge: Iterable[str]
    config_paths: Iterable[str]
    source_directory: str
    target_directory: str
    temp_directory: str | None

def get_environment_config(env_dir: str) -> EnvironmentConfig:
    env_path = os.path.join(env_dir, "env.json")
    with open(env_path) as env_json:
        env = json.load(env_json)

        makemkvcon = env["makemkvcon"] if "makemkvcon" in env else "makemkvcon"
        makemkvcon = makemkvcon if isinstance(makemkvcon, list) else [makemkvcon]
        if makemkvcon[0].startswith(("/", "./", "../")):
            makemkvcon[0] = os.path.join(env_dir, makemkvcon[0])
            if not os.path.isfile(makemkvcon[0]):
                logging.error("Could not find makemkvcon")
                exit(1)

        mkvmerge = env["mkvmerge"] if "mkvmerge" in env else "mkvmerge"
        mkvmerge = mkvmerge if isinstance(mkvmerge, list) else [mkvmerge]
        if mkvmerge[0].startswith(("/", "./", "../")):
            mkvmerge[0] = os.path.join(env_dir, mkvmerge[0])
            if not os.path.isfile(mkvmerge[0]):
                logging.error("Could not find mkvmerge")
                exit(1)

        configs = env["config"] if isinstance(env["config"], list) else [env["config"]]
        config_paths = sum([glob.glob(config, root_dir=env_dir) for config in configs], [])
        source_directory = os.path.join(env_dir, env["source"])
        target_directory = os.path.join(env_dir, env["destination"])
        temp_directory = os.path.join(env_dir, env["temp"]) if "temp" in env else None

        return EnvironmentConfig(makemkvcon=makemkvcon, mkvmerge=mkvmerge, config_paths=config_paths,
            source_directory=source_directory, target_directory=target_directory, temp_directory=temp_directory)