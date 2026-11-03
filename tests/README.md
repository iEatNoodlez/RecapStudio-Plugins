# The plugin list's tests

They run against a Recap Studio checkout (its source, or the dev copy), with that app's own Python:

```powershell
D:\RecapStudio\.venv\Scripts\python tests\run.py --app D:\RecapStudio
python tests\run.py                         # with Recap Studio beside this repository (..\App, ..\RecapStudio)
python tests\run.py youtube spanish         # only the tests whose names have these words
python tests\run.py --network               # also the ones that ask real services (AniList, Kitsu, MangaUpdates)
```

Each test makes a scratch data folder of its own (in the temp folder: removed when every check passed, kept and named when one failed), copies in the plugins it tests (as they are in `features/` and `sources/`, not the zips) and runs the app's server in its own process. Make the recap's steps are swapped for a stand-in where a test needs jobs that finish, fail or stop to ask on cue. Nothing reaches Google: `fake_google.py` stands in for its sign-in and YouTube's API.

| Test | What it covers |
|---|---|
| `test_list.py` | `index.json` is what `tools/build.py` makes (nobody forgot to build), its files match their checksums, every plugin loads in the app |
| `test_anilist.py` | AniList's clean-up of descriptions, the source as the app sees it, Find the series loading; with `--network`, a real search and its characters |
| `test_trending.py` | The Trending finder, with a stand-in for AniList and MangaUpdates: each list as the page gets it (ranks, types, kinds, scores, descriptions without spoilers or MangaUpdates' notes), the filters sent (AniList's left out rather than null, adult series, Lolicon and Shotacon always left out), filters a site hasn't refused, answers kept, MangaUpdates asked a second apart, a busy site; with `--network`, both real lists |
| `test_kitsu.py` | Kitsu's kinds, links and descriptions' clean-up, the source as the app sees it (characters, no chapters); with `--network`, a real search (novels left out), a series' characters and a webtoon it lists none for |
| `test_mangaupdates.py` | MangaUpdates' links (base-36 ids), kinds and descriptions, the source as the app sees it (no chapters, no characters); with `--network`, a real search (novels left out) and a series' details |
| `test_new_chapters.py` | New chapters on a schedule, against a made-up source with pictures served on this PC: the schedule's times, each project's choice (not checked, counted, got, recapped alone, recapped whole), a project with a job running, a source that's down, a time missed while the app was closed, nothing got twice |
| `test_when_done.py` | When a video is done, with a stand-in for ntfy and a webhook: the copy (a folder per project, subtitles, thumbnail, Shorts, nothing half-named), the command (its places quoted, its variables), the phone told (done, stopped to ask once, failed), Discord's and Slack's shapes, a project left out, Do it now, the test button, the command refused from a web page |
| `test_branding.py` | Branding, with made-up projects rendered for real: the logo where it's set, an intro clip (4:3, with sound) and an outro picture on each part, fitted with black bars, joined without decoding errors and without encoding the recap again (on the GPU's encoder and the CPU's, and with an intro from another encoder), the sound in place, subtitles and YouTube chapters moved along, the logo only, nothing, files refused, one taken away |
| `test_overnight_queue.py` | Leaving a project for the morning, waiting for you, the stop time, a start time, carrying on after a restart |
| `test_watched_folder.py` | Settling and half-copied files, nothing added twice or moved, unknown folders, the projects folder refused |
| `test_youtube_post.py` | Connecting (PKCE; a Google account with no channel, the API left off), uploads in pieces through a dropped piece and an expired sign-in, the thumbnail, Shorts one a day, the daily quota, posting again, subtitles and playlists with the wider permission |
| `test_epub.py` | EPUB comics on made-up EPUBs: EPUB 3 and EPUB 2 volumes split into their chapters (front matter, contents, copyright and previews), pages however they're written, one-chapter books, DRM, novels and broken files left out with why; through Import folder, dropped files and the Watched folder. With `RECAP_TEST_EPUB=<an EPUB of yours>`, that one too (read where it is, imported into the test's own folder) |
| `test_spanish.py` | Recaps in Spanish (the voice per provider, the captions, the writers' instructions, nothing saved into the project) and Reading Spanish comics (the language, its reader made ready first; with `RECAP_TEST_MODELS=1`, the reader downloaded into the test's own folder, about 13 MB, each file checked against its pinned checksum, and reading Spanish text, accents and all, with no word turned upside down); the fix for lettering in capitals (¡ read as "i") |

Run them before committing a change to a plugin, and with each app release (plugins use the app's own code).
