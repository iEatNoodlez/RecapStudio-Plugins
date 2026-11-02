# The plugin list's tests

They run against a Recap Studio checkout (its source, or the dev copy), with that app's own Python:

```powershell
D:\RecapStudio\.venv\Scripts\python tests\run.py --app D:\RecapStudio
python tests\run.py                         # with Recap Studio beside this repository (..\App, ..\RecapStudio)
python tests\run.py youtube spanish         # only the tests whose names have these words
python tests\run.py --network               # also the ones that ask real services (AniList)
```

Each test makes a scratch data folder of its own (in the temp folder: removed when every check passed, kept and named when one failed), copies in the plugins it tests (as they are in `features/` and `sources/`, not the zips) and runs the app's server in its own process. Make the recap's steps are swapped for a stand-in where a test needs jobs that finish, fail or stop to ask on cue. Nothing reaches Google: `fake_google.py` stands in for its sign-in and YouTube's API.

| Test | What it covers |
|---|---|
| `test_list.py` | `index.json` is what `tools/build.py` makes (nobody forgot to build), its files match their checksums, every plugin loads in the app |
| `test_anilist.py` | AniList's clean-up of descriptions, the source as the app sees it, Find the series loading; with `--network`, a real search and its characters |
| `test_overnight_queue.py` | Leaving a project for the morning, waiting for you, the stop time, a start time, carrying on after a restart |
| `test_watched_folder.py` | Settling and half-copied files, nothing added twice or moved, unknown folders, the projects folder refused |
| `test_youtube_post.py` | Connecting (PKCE), uploads in pieces through a dropped piece and an expired sign-in, the thumbnail, Shorts one a day, the daily quota, posting again, subtitles and playlists with the wider permission |
| `test_epub.py` | EPUB comics on made-up EPUBs: EPUB 3 and EPUB 2 volumes split into their chapters (front matter, contents, copyright and previews), pages however they're written, one-chapter books, DRM, novels and broken files left out with why; through Import folder, dropped files and the Watched folder. With `RECAP_TEST_EPUB=<an EPUB of yours>`, that one too (read where it is, imported into the test's own folder) |
| `test_spanish.py` | Recaps in Spanish (the voice per provider, the captions, the writers' instructions, nothing saved into the project) and Reading Spanish comics (the language, its reader made ready first; with `RECAP_TEST_MODELS=1`, the reader downloaded into the test's own folder, about 13 MB, each file checked against its pinned checksum, and reading Spanish text, accents and all, with no word turned upside down); the fix for lettering in capitals (¡ read as "i") |

Run them before committing a change to a plugin, and with each app release (plugins use the app's own code).
