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
- **--force** (optional): If the output mkv file already exists, overwrite it instead of skipping the title
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

- **makemkvcon**: Optional path to the makemkvcon executable
- **mkvmerge**: Optional path to the mkvmerge executable
- **source**: Required path to the folder containing all of the BDMV/ISOs
- **destination**: Required path where mkv files should be created
- **config**: Required path to a config file containing movie definitions
- **temp**: Optional working directory instead of the standard temp location

The `media.json` file stores definitions for how to handle each movie
```json
{
    "DISCID": {
        "TITLEID": {
            "name": "Movie Name",
            "year": 2021,
            "audio": [
                {
                    "track": 0,
                    "name": "5.1 DTS-HD Master Audio",
                    "language": "eng",
                    "default": true
                }
            ],
            "subtitle": [
                {
                    "track": 0,
                    "name": "English",
                    "language": "eng"
                },
                {
                    "track": { "index": 0, "forced": true },
                    "name": "English (forced)",
                    "language": "eng",
                    "forced": true
                }
            ]
        }
    }
}
```
- **DISCID**: The name of the folder containing the BDMV/CERTIFICATE/etc folder structure
- **TITLEID**: The 'source file name' in the MakeMKV interface for the desired title
- **name**: The name of the movie/series
- **year**: The release year of the movie/series
- **version**: The specific version of the movie
- **season**: The season of the episode
- **episode**: The episode number within the season
- **extra**: The name of the extra (setting this value marks this title as an extra)
- **type**: The extras folder name to use (only used if `extra` is set) (defaults to `extras`)
- **path**: Where to put the movie relative to `env.json:destination`
- Track options (track order is determined by definition order)
  - **track**: The index of the track in the MakeMKV interface (second audio track is 1)
  - **name**: The display name of the track
  - **language**: The language code for the track
  - **default**: Set true if the track should have the default flag
  - **forced**: Set true if the track should have the forced flag
  - **commentary**: Set true if the track should have the commentary flag
  - **cropping**: Set `{ left, top, right, bottom }` to inform players to crop pixels without reencoding (few players actually support this)

## Extra notes

- For DVD ISOs, set `DISCID="file.iso"` and `TITLEID="01"` using the 'Source title ID' shown in MakeMKV
- Set the env.json config property to an array to split definitions across multiple files
- The env.json config property can use glob format instead of listing every file
- Files are generated at `[destination]/[path]/[name] ([year])/[name] ([year]).mkv`
  - If version is defined then `[destination]/[path]/[name] ([year])/[name] ([year]) - [version].mkv`
  - If season and episode then `[destination]/[path]/[name] ([year])/Season [season]/[name] S[season]E[episode].mkv`
- Values in `DISCID=""` act as defaults for all titles in the file, handy to avoid repeatedly specifying the path or series name
- Values in `TITLEID=""` act as defaults for all titles in the disc, handy to avoid repeatedly specifying the movie name and year
- Some movies have several titles with the same source file, differentiated using an angle number `TITLEID="00245.mpls:1"`
- Track indices are zero indexed and follow MakeMKV UI ordering
  - Core audio and forced subtitle tracks are not counted for track numbering
  - To reference the first audio track's core set `track: { index: 0, core: true }`
  - To reference forced subtitles for a track set `track: { index: 0, forced: true }`
- If video tracks are not specified, the first video track is used with all default values
- If audio tracks are not specified, the first audio track is used with all default values
- If subtitle tracks are not specified, the output will not have any subtitles included
- Use the MakeMKV flatpak by setting env.json makemkvcon to `["flatpak", "run", "--command=makemkvcon", "com.makemkv.MakeMKV"]`
- Use the MKVToolnix flatpak by setting env.json mkvmerge to `["flatpak", "run", "--command=mkvmerge", "org.bunkus.mkvtoolnix-gui"]`

## TODO

- Use a more stable identifier than folder name for discs
- Reference tracks from other titles/BDMVs (ex pull commentary/audio from an older release)
- Improve logging (final summary, warn if an exported track contains forced subtitles, etc)
