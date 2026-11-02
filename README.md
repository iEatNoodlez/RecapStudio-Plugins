# Recap Studio's plugins

The plugin list for [Recap Studio](https://github.com/iEatNoodlez/RecapStudio), made alongside the app. Recap Studio knows this list: **Settings → Plugins → Show the plugins** installs from it, and each file is checked against the SHA-256 in `index.json` first.

| Plugin | Kind | What it does |
|---|---|---|
| **Find the series** | Adds to the app | Search for a series instead of typing its name. It names the project, sets its kind, and adds its characters with their pictures. In New project, and the Characters tab → Find the series…. |
| **Overnight queue** | Adds to the app | Several projects' Make the recap, one after another, from a start time to a stop time. A project that stops to ask you something is left for the morning. In the sidebar. |
| **Watched folder** | Adds to the app | Chapters dropped into a folder named after their project are added to it and, if you like, recapped. In Settings. |
| **Post to YouTube** | Adds to the app | Posts a project's video to your channel (title, chapters, thumbnail, and if you like its subtitles and a playlist) and its Shorts, all at once or one a day: when the video is made, or from the Video tab. Adds a Posting step to New project. Needs your own Google OAuth client: see the wiki's [walkthrough](https://github.com/iEatNoodlez/RecapStudio-Plugins/wiki/Connecting-your-YouTube-channel). |
| **Recaps in Spanish** | Adds to the app | A project's recap narrated in Spanish (Latin America or Spain): the script, the voice for each provider, the subtitles, the captions and the suggested title. In New project and Project settings. |
| **Reading Spanish comics** | Adds to the app | Comics in Spanish read in Spanish (PaddleOCR's Latin-alphabet reader, downloaded the first time) and translated into English, as Korean, Chinese and Japanese ones are. |
| **EPUB comics** | Adds to the app | Chapters imported from a comic's `.epub` (DRM-free), as from a `.cbz`: a volume is split into its chapters by its table of contents, leaving out its contents, copyright and advertisement pages. With Import folder, dropped files and the Watched folder. |
| **AniList** | Source (series details, no chapters) | Search, series details and characters from [AniList's public API](https://docs.anilist.co) (no account or key). |

The docs are in the app's repository: [Plugins](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/plugins.md).

## What goes on this list

- **Plugins that add features** (`features/<id>/`): only this list can install them, since they run inside the app with all its access.
- **Sources for services with public APIs** (`sources/<id>.py`): series details and characters, like AniList.
- **No scrapers**: nothing that gets chapters from scan or aggregator sites. Those stay what members bring themselves (a plugin file, their own list, gallery-dl or Suwayomi). Keeping them off this list keeps a takedown from ever reaching it, or the plugins an edition of the app is built with.

## Changing a plugin

1. Change it, and raise its version: `VERSION` in a source, `version` in a feature's `plugin.json`. The app offers an update when the list's version differs from the one installed.
2. Run `python tools/build.py`. It zips each feature plugin into `dist\` and writes `index.json` with every file's checksum. The zips come out the same every time, so a plugin that didn't change keeps its checksum.
3. Run the tests against Recap Studio: `python tests/run.py --app <its folder>`, with the app's Python (see [tests/README.md](tests/README.md)). They also check that `index.json` was built.
4. Commit `index.json` and `dist\` with the change.

How plugins are written (what `setup(plugin)` and the page script are given, and the list's format) is in [docs/plugins.md](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/plugins.md#how-plugins-are-made) and, for sources, [docs/sources.md](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/sources.md#writing-a-plugin).

## License

MIT, like the app. The data the sources fetch belongs to those services and is under their terms (AniList's API: free for non-commercial use).
