from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import logging

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

MAKEMKV_MSG_DUPLICATETITLE = 3309

@dataclass(kw_only=True)
class BdmvTitleInfo:
    title_id: str
    output_file: str
    output_bytes: int
    video_streams: Iterable[int]
    audio_streams: Iterable[int]
    subtitle_streams: Iterable[int]
    derived_streams: Iterable[int]

@dataclass(kw_only=True)
class BdmvInfo:
    name: str
    path: str
    titles: Mapping[str, BdmvTitleInfo]

def parse_bdmv_info(name: str, path: str, makemkv_info: str) -> BdmvInfo:
    title_ids = set()
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
    duplicate_source = {}

    for line in makemkv_info.splitlines():
        if line.startswith("TINFO:"):
            [title, field, code, value] = line[6:].split(",", 3)
            title_ids.add(title)
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
            title_ids.add(title)
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
        if line.startswith("MSG:"):
            [code, flags, count, text, text_format, *values] = line[4:].split(",")
            if int(code) == MAKEMKV_MSG_DUPLICATETITLE:
                duplicate_source[values[0].strip('"')] = values[1].strip('"')

    titles: Mapping[str, BdmvTitleInfo] = {}
    for title in title_ids:
        if title not in title_output:
            logging.critical("Did not get an output file for title %s in %s", title, name)
            exit(1)
        if title not in title_bytes:
            logging.critical("Did not get an output size for title %s in %s", title, name)
            exit(1)
        titles[title] = BdmvTitleInfo(title_id=title, output_file=title_output[title], output_bytes=title_bytes[title],
            video_streams=stream_video.get(title, []), audio_streams=stream_audio.get(title, []),
            subtitle_streams=stream_subtitle.get(title, []), derived_streams=stream_derived.get(title, []))

    source_title: Mapping[str, BdmvTitleInfo] = {}
    for title in title_file:
        if title not in title_angle: source_title[title_file[title]] = titles[title]
        else: source_title[f"{title_file[title]}({title_angle[title]})"] = titles[title]
    for title in title_originalid:
        source_title[title_originalid[title]] = titles[title]
    for title in title_comment:
        source_title[title_comment[title]] = titles[title]
    for source, target in duplicate_source.items():
        if source in source_title:
            logging.critical("Title %s in %s was listed as a duplicate but has data", source, name)
            exit(1)
        for _ in range(0, len(duplicate_source) + 1):
            if target not in duplicate_source: break
            target = duplicate_source[target]
        else:
            logging.critical("Title %s in %s is a duplicate of itself", source, name)
            exit(1)
        if target not in source_title:
            logging.critical("Title %s in %s is a duplicate of nonexistant title %s", source, name, target)
            exit(1)
        source_title[source] = source_title[target]

    logging.debug("Identified titles: %s", source_title.keys())
    return BdmvInfo(name=name, path=path, titles=source_title)