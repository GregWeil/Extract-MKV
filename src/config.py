from collections.abc import Iterable
from dataclasses import dataclass
import logging

from src.bdmvkey import BdmvTitleKey, parse_bdmv_key

@dataclass(frozen=True, eq=True)
class StreamSourceKey:
    title: BdmvTitleKey
    type: str
    index: int
    derived: bool

@dataclass(kw_only=True)
class CroppingConfig:
    left: int
    top: int
    right: int
    bottom: int

@dataclass(kw_only=True)
class StreamConfig:
    source: StreamSourceKey
    name: str | None
    language: str | None
    default: bool | None
    forced: bool | None
    commentary: bool | None
    cropping: CroppingConfig | None

@dataclass(kw_only=True)
class OutputConfig:
    path: Iterable[str]
    name: str
    year: int | None
    version: str | None
    season: int | None
    episode: int | None
    episode_title: str | None
    extra_title: str | None
    extra_type: str | None
    video_streams: Iterable[StreamConfig]
    audio_streams: Iterable[StreamConfig]
    subtitle_streams: Iterable[StreamConfig]

    def all_streams(self):
        return [*self.video_streams, *self.audio_streams, *self.subtitle_streams]

def parse_stream_source_config(config_json: any, stream_type: str, context: BdmvTitleKey):
    if isinstance(config_json, int):
        return StreamSourceKey(title=context, type=stream_type, index=config_json, derived=False)
    if not isinstance(config_json, dict):
        logging.critical("Invalid stream track %r in %s %s", config_json, context.bdmv.name, context.title)
        exit(1)
    title = context
    if "source" in config_json:
        title = BdmvTitleKey(bdmv=parse_bdmv_key(config_json["source"]), title=str(config_json["title"]))
    elif "title" in config_json:
        title = BdmvTitleKey(bdmv=context.bdmv, title=str(config_json["title"]))
    index = int(config_json["index"])
    derived = bool(config_json.get("core", False)) or bool(config_json.get("forced", False))
    return StreamSourceKey(title=title, type=stream_type, index=index, derived=derived)

def parse_cropping_config(config_json: any, context: BdmvTitleKey):
    if not isinstance(config_json, dict):
        logging.critical("Invalid cropping config %r in %s %s", config_json, context.bdmv.name, context.title)
        exit(1)
    left = int(config_json.get("left", 0))
    top = int(config_json.get("top", 0))
    right = int(config_json.get("right", 0))
    bottom = int(config_json.get("bottom", 0))
    return CroppingConfig(left=left, top=top, right=right, bottom=bottom)

def parse_stream_config(config_json: any, stream_type: str, context: BdmvTitleKey):
    if not isinstance(config_json, dict):
        logging.critical("Invalid stream config %r in %s %s", config_json, context.bdmv.name, context.title)
        exit(1)
    source = parse_stream_source_config(config_json["track"], stream_type, context)
    name = str(config_json["name"]) if "name" in config_json else None
    language = str(config_json["language"]) if "language" in config_json else None
    default = bool(config_json["default"]) if "default" in config_json else None
    forced = bool(config_json["forced"]) if "forced" in config_json else None
    commentary = bool(config_json["commentary"]) if "commentary" in config_json else None
    cropping = parse_cropping_config(config_json["cropping"]) if "cropping" in config_json else None
    return StreamConfig(source=source, name=name, language=language, default=default, forced=forced, commentary=commentary, cropping=cropping)

def parse_stream_configs(config_json: any, stream_type: str, context: BdmvTitleKey):
    if not isinstance(config_json, list):
        logging.critical("Invalid stream list %r in %s %s", config_json, context.bdmv.name, context.title)
        exit(1)
    stream_configs: list[StreamConfig] = [parse_stream_config(stream_json, stream_type, context) for stream_json in config_json]
    if any([stream_config.default for stream_config in stream_configs]):
        for stream_config in stream_configs:
            if stream_config.default is not None: continue
            stream_config.default = False
    return stream_configs

def parse_output_config(config_json: any, context: BdmvTitleKey):
    if not isinstance(config_json, dict):
        logging.critical("Invalid config %r in %s %s", config_json, context.bdmv.name, context.title)
        exit(1)
    path = []
    if "path" in config_json:
        path_value = config_json["path"] if isinstance(config_json["path"], list) else [config_json["path"]]
        path = [str(segment) for segment in path_value]
    name = str(config_json["name"])
    year = int(config_json["year"]) if "year" in config_json else None
    version = str(config_json["version"]) if "version" in config_json else None
    season = int(config_json["season"]) if "season" in config_json else None
    episode = int(config_json["episode"]) if "episode" in config_json else None
    episode_title = str(config_json["title"]) if "title" in config_json else None
    if (episode_title and episode is None) or (episode is not None and season is None):
        logging.critical("Invalid series config in %s %s", context.bdmv.name, context.title)
        exit(1)
    extra_title = str(config_json["extra"]) if "extra" in config_json else None
    extra_type = str(config_json["type"]) if "type" in config_json else ("extras" if extra_title else None)
    if extra_type and not extra_title:
        logging.critical("Invalid extra config in %s %s", context.bdmv.name, context.title)
        exit(1)
    video_streams = parse_stream_configs(config_json.get("video", [{ "track": 0 }]), "video", context)
    audio_streams = parse_stream_configs(config_json.get("audio", [{ "track": 0 }]), "audio", context)
    subtitle_streams = parse_stream_configs(config_json.get("subtitle", []), "subtitle", context)
    return OutputConfig(path=path, name=name, year=year, version=version,
        season=season, episode=episode, episode_title=episode_title, extra_title=extra_title, extra_type=extra_type,
        video_streams=video_streams, audio_streams=audio_streams, subtitle_streams=subtitle_streams)