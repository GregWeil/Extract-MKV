# Extract-MKV

A script to remux blurays into mkv files using MakeMKV in a repeatable way.
MakeMKV's interface is fine, but movies can have several tracks that need to be identified and tagged, and once you've closed the program that info is lost and all needs to be redone.
By defining in a json file what should be done with each movie, it can be rerun and tweaked to standardize the library or fix mistakes without configuring everything from scratch.

## How it works

MakeMKV's command line does not provide most of the options available in the interface, so two passes are needed.
1. MakeMKV extracts the chosen title with all available streams included
2. MKVToolNix's mkvmerge is used to remux the streams and set attributes

## Usage

`python ./extract-mkv.py SELECTION`

- **SELECTION**: A comma separated list of disc identifiers, or ALL to extract all discs
- **--verbose** (optional): Log out extra information, as well as MakeMKV and MKVToolNix output

## Configuration

Create an `env.json` file next to the main script containing the following properties
```json
{
    "makemkvcon": "C:\\path\\to\\makemkvcon64.exe",
    "mkvmerge": "C:\\path\\to\\mkvmerge.exe",
    "source": "path\\to\\bdmv\\folders",
    "destination": "path\\to\\put\\mkvs",
    "config": "path\\to\\media.json"
}
```

The `media.json` file stores definitions for how to handle each movie
```json
{
    "DISCID": {
        "TITLEID": {
            "name": "NAME",
            "audio": [
                {
                    "track": 1,
                    "name": "5.1 DTS-HD Master Audio",
                    "language": "eng",
                    "default": true
                }
            ],
            "subtitle": [
                {
                    "track": 7,
                    "name": "English",
                    "language": "eng"
                }
            ]
        }
    }
}
```
- **DISCID**: The name of the folder containing the BDMV/CERTIFICATE/etc folder structure
- **TITLEID**: The 'source file name' in the MakeMKV interface for the desired title
- **NAME**: The output file for this title will be `[env.json:destination]/[NAME].mkv`
- Track options (output order is determined by order in the definition)
  - **track**: The index of the track in the MakeMKV interface
  - **name**: The display name of the track
  - **language**: The language code for the track
  - **default**: Set true if the track should have the default flag
  - **forced**: Set true if the track should have the forced flag
  - **commentary**: Set true if the track should have the commentary flag

## Extra notes

- Some movies have several titles with the same source file, differentiated using an angle number. In this case the TITLEID would be something like `00245.mpls:1`
- Track indices start at 0 and count up, continuing through all track types without restarting
  - Core audio tracks are included when counting tracks
  - Forced subtitle tracks are only counted if forced subtitles exist
- Video is assumed to be track 0 and always included, there is currently no way to customize how video tracks are handled

## TODO

- Support for DVD isos using MakeMKV's 'source title id' as the TITLEID
- Output subfolder support for movies with multiple cuts or special features
- Specify chapter names in the definition
