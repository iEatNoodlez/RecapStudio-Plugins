"""Post to YouTube against a stand-in for Google (fake_google.py), on a scratch data folder: the client, connecting
(PKCE, the one-time address), posting when the video is made (title, description with chapters, privacy, pieces, a
dropped piece, an expired sign-in, the thumbnail), Shorts one a day, the daily quota, posting again, subtitles and
playlists with the wider permission, and disconnecting. Nothing reaches Google."""
import hashlib
import json
import os
import time
import urllib.parse

import cv2
import numpy as np
import requests

import fake_google
from helpers import App, check, done, until

google = fake_google.start()
G = f"http://127.0.0.1:{google.server_address[1]}"
app = App(plugins=["youtube-post"], env={"RECAP_YT_AUTH": f"{G}/auth", "RECAP_YT_TOKEN": f"{G}/token", "RECAP_YT_API": G})
pipeline, plugins, store, DATA = app.pipeline, app.plugins, app.store, app.data
B = f"{app.base}/api/plugins/youtube-post"
yt = app.module("youtube-post")
check("plugin loaded", app.loaded("youtube-post"), plugins.loaded.get("youtube-post"))
opened = []
yt.webbrowser.open = lambda url: opened.append(url) or True  # (the test is the browser)

# ---- a project with a made video: 20 MB (three pieces of 8 MB), chapters, a thumbnail, three Shorts ----
pid = store.create("Give My Regards to Black Jack", {**store.new_project_settings(), "orientation": "portrait", "shorts": 60})["id"]
d = store.project_dir(pid)
(d / "output" / "shorts").mkdir(parents=True, exist_ok=True)
video = os.urandom(20 << 20)
(d / "output" / "recap.mp4").write_bytes(video)
(d / "output" / "recap - YouTube chapters.txt").write_text("0:00 Ep.01 Night of the Intern\n", encoding="utf-8")
SRT = "1\n00:00:00,000 --> 00:00:03,200\nEijiro Saito starts his first night shift.\n"
(d / "output" / "recap.srt").write_text(SRT, encoding="utf-8", newline="\n")
cv2.imwrite(str(d / "output" / "thumbnail.jpg"), np.full((720, 1280, 3), 120, np.uint8))
for k in (1, 2, 3):
    (d / "output" / "shorts" / f"recap - Short {k:02}.mp4").write_bytes(os.urandom(300_000 + k))
store.update(pid, lambda p: p.update(
    output={"video": "output/recap.mp4", "parts": [{"video": "output/recap.mp4", "srt": "output/recap.srt",
                                                     "chapters": "output/recap - YouTube chapters.txt", "seconds": 673.6}]},
    youtube={"title": "I Made 2x My Salary in ONE Night… Then They Left Me Alone With a Dying Patient | Manga Recap",
             "file": "output/thumbnail.jpg"}))

# ---- the client and connecting ----
r = requests.post(f"{B}/projects/{pid}/post", json={})
check("posting refused before connecting", r.status_code == 400 and "Connect" in r.json()["detail"], r.text)
r = requests.put(f"{B}/client", json={"client_id": "not-a-client", "client_secret": "x"})
check("a wrong client ID refused", r.status_code == 400, r.text)
r = requests.put(f"{B}/client", json={"client_id": "1234-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-test"})
check("client saved, secret not shown", r.status_code == 200 and r.json()["client"] and "GOCSPX" not in r.text, r.text)
r = requests.post(f"{B}/connect")
url = urllib.parse.urlparse(opened[-1])
q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
check("browser opened on the sign-in page", url.geturl().startswith(f"{G}/auth") and q["code_challenge_method"] == "S256"
      and q["redirect_uri"].startswith("http://127.0.0.1:") and q["access_type"] == "offline"
      and "youtube.upload" in q["scope"], q)
fake_google.SEEN["challenge"] = q["code_challenge"]
page = requests.get(q["redirect_uri"], params={"code": "good-code", "state": "someone-else"}).text
check("an answer for another sign-in is refused", "wasn't for this sign-in" in page, page[-200:])
requests.post(f"{B}/connect")
url = urllib.parse.urlparse(opened[-1])
q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
fake_google.SEEN.update(challenge=q["code_challenge"], scope=q["scope"])  # (Google grants what was asked)
check("only upload and read asked for, at first", "youtube.force-ssl" not in q["scope"], q["scope"])
page = requests.get(q["redirect_uri"], params={"code": "good-code", "state": q["state"]}).text
check("signed in (PKCE checked by Google)", "Connected to Test Channel" in page and fake_google.SEEN["verifier_ok"], page[-200:])
a = requests.get(f"{B}/account").json()
check("channel shown", a["channel"] == "Test Channel" and a["connect"]["state"] == "done", a)
token = json.loads((DATA / "plugin-data" / "youtube-post" / "token.json").read_text())
check("only the token is kept, with a refresh token", token["refresh_token"] == "rt-1", token)

# ---- New project's choice, then the video is made ----
r = requests.put(f"{B}/projects/{pid}", json={"choice": {"when": "auto", "privacy": "unlisted", "shorts": "daily", "daily_at": "18:00"}})
check("choice saved", r.status_code == 200 and r.json()["choice"]["when"] == "auto", r.text)
check("bad choice refused", requests.put(f"{B}/projects/{pid}", json={"choice": {"privacy": "everyone"}}).status_code == 400)
items = r.json()["items"]
check("what it posts: the video, then three Shorts after it", [(i["kind"], i["state"]) for i in items]
      == [("video", "new"), ("short", "waiting"), ("short", "waiting"), ("short", "waiting")], items)
fake_google.CONFIG.update(fail_piece_once=2, expire_after_pieces=None)
yt._after_job({"status": "done", "steps": ["panels", "video"], "project": pid})  # (as when Make the recap finishes)
check("posting job ran", until(lambda: (pipeline.latest_for(pid) or {}).get("status") == "done", 60),
      pipeline.latest_for(pid))
job = pipeline.latest_for(pid)
up = fake_google.SEEN["uploads"]
check("one upload: the video", len(up) == 1, len(up))
m = up[0]["meta"]
check("whole and exact, through a dropped piece", up[0]["sha"] == hashlib.sha256(video).hexdigest() and fake_google.SEEN["failed_once"])
check("title, privacy, not for kids", m["snippet"]["title"].startswith("I Made 2x My Salary") and m["status"]["privacyStatus"] == "unlisted"
      and m["status"]["selfDeclaredMadeForKids"] is False, m)
check("description has the chapter list", "0:00 Ep.01 Night of the Intern" in m["snippet"]["description"], m["snippet"]["description"])
check("thumbnail set", fake_google.SEEN["thumbs"] and fake_google.SEEN["thumbs"][0]["video"] == up[0]["id"])
check("job says so", "Posted 1 to YouTube" in job.get("message", ""), job.get("message"))
st = requests.get(f"{B}/projects/{pid}").json()
check("video posted with its link; Shorts scheduled from the next 18:00",
      st["items"][0]["state"] == "posted" and st["items"][0]["url"] == f"https://youtu.be/{up[0]['id']}"
      and [i["state"] for i in st["items"][1:]] == ["due", "due", "due"] and st["items"][1]["due"].endswith("18:00"), st["items"])

# ---- Shorts one a day: the first comes due ----
sched = DATA / "plugin-data" / "youtube-post" / "schedule.json"
first = json.loads(sched.read_text())[pid]
sched.write_text(json.dumps({pid: time.time() - 60}))  # (as if 18:00 came)
fake_google.CONFIG.update(expire_after_pieces=fake_google.SEEN["pieces"])  # (the sign-in runs out on the next piece)
yt._look()
check("first Short posted when due, through an expired sign-in", until(lambda: len(fake_google.SEEN["uploads"]) == 2
      and pipeline.latest_for(pid)["status"] == "done", 30) and fake_google.SEEN["refreshes"] >= 1, len(fake_google.SEEN["uploads"]))
sm = fake_google.SEEN["uploads"][1]["meta"]
check("the Short's title and description", sm["snippet"]["title"].endswith("(Part 1)") and "#Shorts" in sm["snippet"]["description"], sm)
check("a title that only fits without its | Manga Recap: that left out, not cut partway",
      sm["snippet"]["title"] == "I Made 2x My Salary in ONE Night… Then They Left Me Alone With a Dying Patient (Part 1)",
      sm["snippet"]["title"])
long = "They Laughed at His BROKEN Maid… Until She Solo'd An Entire SSS-Rank GUILD and Then the Whole Kingdom Bowed"
check("a long title without one: cut after a whole word", yt._fit(long, " (Part 2)")
      == "They Laughed at His BROKEN Maid… Until She Solo'd An Entire SSS-Rank GUILD and Then the… (Part 2)",
      yt._fit(long, " (Part 2)"))
yt._look()
time.sleep(1)
check("the second waits for its day", len(fake_google.SEEN["uploads"]) == 2)

# ---- the daily quota ----
sched.write_text(json.dumps({pid: time.time() - 3 * 86400}))  # (both left are due)
fake_google.CONFIG["quota"] = True
yt._look()
until(lambda: pipeline.latest_for(pid)["status"] == "done", 30)
qfile = DATA / "plugin-data" / "youtube-post" / "quota.json"
check("quota used up: stops, says when it carries on", qfile.exists() and "daily limit" in pipeline.latest_for(pid)["message"]
      and json.loads((DATA / "plugin-data" / "youtube-post" / "pending.json").read_text()).get(pid), pipeline.latest_for(pid)["message"])
before = pipeline.latest_for(pid)["id"]
yt._look()
time.sleep(1)
check("nothing tried while the quota is used up", pipeline.latest_for(pid)["id"] == before)
fake_google.CONFIG["quota"] = False
qfile.unlink()  # (the next day)
yt._look()
check("the rest go up the next day", until(lambda: len(fake_google.SEEN["uploads"]) == 4 and pipeline.latest_for(pid)["status"] == "done", 30),
      len(fake_google.SEEN["uploads"]))
check("nothing pending after", not json.loads((DATA / "plugin-data" / "youtube-post" / "pending.json").read_text()))

# ---- posting again, the thumbnail refused, landscape ----
r = requests.post(f"{B}/projects/{pid}/post", json={})
until(lambda: pipeline.latest_for(pid)["status"] == "done", 20)
check("nothing posted twice", len(fake_google.SEEN["uploads"]) == 4 and "Nothing new" in pipeline.latest_for(pid)["message"],
      pipeline.latest_for(pid)["message"])
time.sleep(1.1)
(d / "output" / "recap.mp4").write_bytes(video[: 9 << 20])  # (the video made again)
st = requests.get(f"{B}/projects/{pid}").json()
check("made again since it was posted", st["items"][0]["state"] == "changed", st["items"][0])
fake_google.CONFIG["thumb_forbidden"] = True
requests.post(f"{B}/projects/{pid}/post", json={"again": True})
until(lambda: pipeline.latest_for(pid)["status"] == "done", 30)
check("posted again when asked; a refused thumbnail is a note", len(fake_google.SEEN["uploads"]) == 5
      and "thumbnail not set" in pipeline.latest_for(pid)["message"], pipeline.latest_for(pid)["message"])
store.update(pid, lambda p: p["settings"].update(orientation="landscape"))
st = requests.get(f"{B}/projects/{pid}").json()
check("a landscape project posts no Shorts", [i["kind"] for i in st["items"]] == ["video"] and not st["portrait"], st["items"])
yt._after_job({"status": "error", "steps": ["video"], "project": pid})
time.sleep(0.5)
check("a failed render posts nothing", pipeline.latest_for(pid)["status"] == "done" and len(fake_google.SEEN["uploads"]) == 5)

# ---- subtitles and playlists ----
store.update(pid, lambda p: p["settings"].update(orientation="portrait"))
r = requests.put(f"{B}/projects/{pid}", json={"choice": {"subtitles": True, "playlist": "new", "playlist_shorts": True}})
check("subtitles and playlist chosen", r.status_code == 200 and r.json()["choice"]["playlist"] == "new", r.text)
check("a bad playlist refused", requests.put(f"{B}/projects/{pid}", json={"choice": {"playlist": "../x"}}).status_code == 400)
requests.post(f"{B}/projects/{pid}/post", json={})
until(lambda: pipeline.latest_for(pid)["status"] == "done", 20)
msg = pipeline.latest_for(pid)["message"]
check("without the wider permission: says so, adds nothing", "wider permission" in msg and not fake_google.SEEN["captions"]
      and not fake_google.SEEN["playlist_items"], msg)
a = requests.put(f"{B}/settings", json={"settings": {"more": "yes"}}).json()
check("switched on, not granted yet", a["settings"]["more"] == "yes" and a["more"] is False, a)
requests.post(f"{B}/connect")
url = urllib.parse.urlparse(opened[-1])
q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
check("connecting again asks for it too", "youtube.force-ssl" in q["scope"] and q["include_granted_scopes"] == "true", q["scope"])
fake_google.SEEN.update(challenge=q["code_challenge"], scope=q["scope"])
requests.get(q["redirect_uri"], params={"code": "good-code", "state": q["state"]})
check("granted", requests.get(f"{B}/account").json()["more"] is True)
lists = requests.get(f"{B}/playlists").json()["playlists"]
check("the channel's playlists listed", lists == [{"id": "PL-old", "title": "Old stuff"}], lists)
requests.post(f"{B}/projects/{pid}/post", json={})
until(lambda: pipeline.latest_for(pid)["status"] == "done", 30)
msg = pipeline.latest_for(pid)["message"]
video_id = fake_google.SEEN["uploads"][-1]["id"]
caps = fake_google.SEEN["captions"]
check("subtitles uploaded for the posted video, word for word", len(caps) == 1 and caps[0]["meta"]["snippet"]["videoId"] == video_id
      and caps[0]["meta"]["snippet"]["language"] == "en" and caps[0]["srt"] == SRT.strip("\n"), caps)
made = fake_google.SEEN.get("playlists_made") or []
check("a playlist named after the series, made once", len(made) == 1 and made[0]["snippet"]["title"] == "Give My Regards to Black Jack", made)
items = fake_google.SEEN["playlist_items"]
check("the video and its Shorts added to it", len(items) == 4 and items[0] == (made[0]["id"], video_id)
      and {v for _, v in items[1:]} == {u["id"] for u in fake_google.SEEN["uploads"][1:4]}, items)
check("no new uploads for that", len(fake_google.SEEN["uploads"]) == 5)
st = requests.get(f"{B}/projects/{pid}").json()
check("each one says so", st["items"][0]["subtitles"] and all(i["playlist"] for i in st["items"]) and st["choice"]["playlist"] == made[0]["id"],
      st["items"])
requests.post(f"{B}/projects/{pid}/post", json={})
until(lambda: pipeline.latest_for(pid)["status"] == "done", 20)
check("nothing done twice", len(fake_google.SEEN["captions"]) == 1 and len(fake_google.SEEN["playlist_items"]) == 4
      and "Nothing new" in pipeline.latest_for(pid)["message"], pipeline.latest_for(pid)["message"])

# ---- disconnecting ----
a = requests.post(f"{B}/disconnect").json()
check("disconnected and access taken back", a["channel"] is None and "rt-1" in fake_google.SEEN["revoked"], a)

# ---- a Google account with no YouTube channel (YouTube: 401 Unauthorized, youtubeSignupRequired) ----
fake_google.CONFIG["no_channel"] = True
requests.post(f"{B}/connect")
url = urllib.parse.urlparse(opened[-1])
q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
fake_google.SEEN.update(challenge=q["code_challenge"], scope=q["scope"])
page = requests.get(q["redirect_uri"], params={"code": "good-code", "state": q["state"]}).text
check("no channel: said in plain words", "Unauthorized. That Google account has no YouTube channel" in page
      and "Brand Account" in page, page[-300:])
a = requests.get(f"{B}/account").json()
check("no channel: not connected, and no sign-in kept", a["channel"] is None and a["connect"]["state"] == "error"
      and not (DATA / "plugin-data" / "youtube-post" / "token.json").exists(), a)
fake_google.CONFIG.update(no_channel=False, api_off=True)
requests.post(f"{B}/connect")
url = urllib.parse.urlparse(opened[-1])
q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
fake_google.SEEN.update(challenge=q["code_challenge"], scope=q["scope"])
page = requests.get(q["redirect_uri"], params={"code": "good-code", "state": q["state"]}).text
check("the API not turned on: said what to turn on", "Turn on the YouTube Data API v3" in page, page[-300:])
fake_google.CONFIG["api_off"] = False
done()
