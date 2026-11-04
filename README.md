# Recap Studio's plugins

The plugin list for [Recap Studio](https://github.com/iEatNoodlez/RecapStudio), made alongside the app. Recap Studio knows this list: **Settings → Plugins → Show the plugins** installs from it, and each file is checked against the SHA-256 in `index.json` first.

| Plugin | Type | What it does |
|---|---|---|
| **Find the series** | Series lookup | Search for a series instead of typing its name. It names the project, sets its kind, and adds its characters with their pictures. In New project, and the Characters tab → Find the series…. |
| **Trending finder** | Series discovery | The series trending now on [AniList](https://anilist.co) and looked at most on [MangaUpdates](https://www.mangaupdates.com), by kind (manga, manhwa, manhua), genre and whether it's finished, with the ones you have a project of marked. **Make a project** opens New project for one, with Find the series picking it from the same site, then looks for its chapters in your own sources (or gets them straight away). In the sidebar. |
| **AniList** | Series details source | Search, series details and characters from [AniList's public API](https://docs.anilist.co) (no account or key). |
| **Kitsu** | Series details source | Search, series details and characters from [Kitsu's public API](https://kitsu.docs.apiary.io) (no account or key). Much of its character data came from MyAnimeList, so it stands in for it. Its characters are thorough for older and well-known Japanese manga, and missing for most newer series and webtoons. |
| **MangaUpdates** | Series details source | Search and series details from [MangaUpdates' public API](https://api.mangaupdates.com) (no account or key). It lists nearly every series that has been scanlated, so it finds many AniList doesn't have. It has no characters. |
| **Site check** | Sources helper | When a site answers your sources with Cloudflare's check for a person ("Just a moment…"), **Open the site** opens it in a small window, where you pass the check yourself: wait while it runs, or tick its box. Your source plugins then reach that site with your pass until it runs out. Nothing in the window is done for you. Needs Recap Studio 0.2.4. In Get chapters and Settings. |
| **Overnight queue** | Automation | Several projects' Make the recap, one after another, from a start time to a stop time. A project that stops to ask you something is left for the morning. In the sidebar. |
| **Watched folder** | Automation | Chapters dropped into a folder named after their project are added to it and, if you like, recapped. In Settings. |
| **New chapters on a schedule** | Automation | Projects made from a site are checked for new chapters on the days and at the time you choose, through the sources you added yourself. Each project chooses: tell me, get them, or get them and recap them (just the new chapters, or the whole project). In Settings and Project settings. |
| **When a video is done** | Automation | Once a project's video is made: copy it (with its subtitles, thumbnail and Shorts) to a folder, run a command of yours, and tell your phone through [ntfy](https://ntfy.sh) or a Discord or Slack webhook. It can also tell you when a recap stops to ask you something or fails. In Settings. |
| **Post to YouTube** | Publishing | Posts a project's video to your channel (title, chapters, thumbnail, and if you like its subtitles and a playlist) and its Shorts, all at once or one a day: when the video is made, or from the Video tab. Adds a Posting step to New project. Needs your own Google OAuth client: see the wiki's [walkthrough](https://github.com/iEatNoodlez/RecapStudio-Plugins/wiki/Connecting-your-YouTube-channel). |
| **Branding** | Video addition | Your channel's logo in a corner of every video, and an intro and an outro (a clip or a picture) before and after each part. Set once in Settings; each project can leave either out. |
| **Reading Spanish comics** | Language addition (comics) | Comics in Spanish read in Spanish (PaddleOCR's Latin-alphabet reader, downloaded the first time) and translated into English, as Korean, Chinese and Japanese ones are. |
| **Recaps in Spanish** | Language addition (narration) | A project's recap narrated in Spanish (Latin America or Spain): the script, the voice for each provider, the subtitles, the captions and the suggested title. In New project and Project settings. |
| **EPUB comics** | File format addition | Chapters imported from a comic's `.epub` (DRM-free), as from a `.cbz`: a volume is split into its chapters by its table of contents, leaving out its contents, copyright, advertisement and divider pages. With Import folder, dropped files and the Watched folder. |

### The types

- **Series lookup**: fills in a new project from what a site knows about the series (its name, kind and characters), so you don't type them. It searches the series details sources below, and any source plugins of your own that can search.
- **Series discovery**: finds series worth making a recap of, from what's trending on sites with public APIs, and starts a project of one.
- **Series details source**: a service with a public API that Series lookup searches. It gives a series' details, and its characters when the service lists them, but never chapters. It works as soon as it's installed, with no restart.
- **Sources helper**: helps the sources you added yourself reach a site. It gets nothing by itself.
- **Automation**: does the app's work for you on a timer or when something happens (a time of day, a file dropped in a folder, a video finished), while Recap Studio is open.
- **Publishing**: sends finished recaps somewhere outside the app, such as your YouTube channel.
- **Video addition**: adds something of yours to every video the app makes, such as a logo, an intro and an outro.
- **Language addition**: a language the app didn't have.
  - **Comics**: comics in that language are read in it, then translated into English for the script writer, as Korean, Chinese and Japanese ones are.
  - **Narration**: recaps written and voiced in that language, whatever language the comic is in.
- **File format addition**: a kind of file chapters can be imported from, wherever the app imports them (Import folder, files dropped on a project, the Watched folder).

The docs are in the app's repository: [Plugins](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/plugins.md).

## What goes on this list

- **Plugins that add features** (`features/<id>/`): only this list can install them, since they run inside the app with all its access.
- **Sources for services with public APIs** (`sources/<id>.py`): series details and characters, like AniList, Kitsu and MangaUpdates.
- **No scrapers**: nothing that gets chapters from scan or aggregator sites. Those stay what members bring themselves (a plugin file, their own list, gallery-dl or Suwayomi). Keeping them off this list keeps a takedown from ever reaching it, or the plugins an edition of the app is built with. New chapters on a schedule gets nothing by itself: it checks through the sources a member added.

## Changing a plugin

1. Change it, and raise its version: `VERSION` in a source, `version` in a feature's `plugin.json`. The app offers an update when the list's version differs from the one installed.
2. Run `python tools/build.py`. It zips each feature plugin into `dist\` and writes `index.json` with every file's checksum. The zips come out the same every time, so a plugin that didn't change keeps its checksum.
3. Run the tests against Recap Studio: `python tests/run.py --app <its folder>`, with the app's Python (see [tests/README.md](tests/README.md)). They also check that `index.json` was built.
4. Commit `index.json` and `dist\` with the change.

How plugins are written (what `setup(plugin)` and the page script are given, and the list's format) is in [docs/plugins.md](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/plugins.md#how-plugins-are-made) and, for sources, [docs/sources.md](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/sources.md#writing-a-plugin).

## License

MIT, like the app. The data the sources fetch belongs to those services and is under their terms (AniList's API: free for non-commercial use; MangaUpdates' asks to be credited, which Find the series and the Trending finder do by naming the source; Kitsu's is free, with no key).
