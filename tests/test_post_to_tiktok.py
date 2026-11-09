"""Post to TikTok against a stand-in for TikTok (fake_tiktok.py), on a scratch data folder: the app's key and secret,
connecting (PKCE with TikTok's hex challenge, the one-time address, a renewed sign-in's new refresh token kept),
posting one video (in pieces, through a dropped piece; only you see it until the app is audited, and a public account
is refused then), a series of three parts in order (their captions, the series' hashtag, a pause between them, "too
many requests" waited out, nothing posted twice, a part made again, a post TikTok was still processing looked up
rather than sent again), public only once TikTok says so, a video too long for the account, the Shorts, Advanced
Cutting's pieces when the video is made, TikTok's daily limit and the rest later, disconnecting, and on a server the
address pasted. Nothing reaches TikTok."""
import hashlib
import json
import os
import re
import time
import urllib.parse

import requests

import fake_tiktok
from helpers import App, check, done, until

tiktok = fake_tiktok.start()
T = f"http://127.0.0.1:{tiktok.server_address[1]}"
app = App(plugins=["post-to-tiktok"], env={"RECAP_TT_AUTH": f"{T}/v2/auth/authorize/", "RECAP_TT_API": T})
pipeline, plugins, store, DATA = app.pipeline, app.plugins, app.store, app.data
B = f"{app.base}/api/plugins/post-to-tiktok"
tt = app.module("post-to-tiktok")
check("plugin loaded", app.loaded("post-to-tiktok"), plugins.loaded.get("post-to-tiktok"))
tt.GAP, tt.BACKOFF, tt.POLL = 0.5, 0.2, 0.05  # (seconds, not TikTok's real pauses)
opened = []
tt.webbrowser.open = lambda url: opened.append(url) or True  # (the test is the browser)
FILES = DATA / "plugin-data" / "post-to-tiktok"
SEEN, CONFIG = fake_tiktok.SEEN, fake_tiktok.CONFIG
MB = 1 << 20


def finished(pid: str, seconds: float = 60) -> dict:
    until(lambda: (pipeline.latest_for(pid) or {}).get("status") in ("done", "error", "cancelled"), seconds)
    return pipeline.latest_for(pid)


def query(url: str) -> dict:
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def project(name: str, parts: list, **settings):
    """A project with a made video: its parts' sizes and lengths, as the app records them."""
    pid = store.create(name, {**store.new_project_settings(), **settings})["id"]
    d = store.project_dir(pid)
    (d / "output").mkdir(parents=True, exist_ok=True)
    out = []
    for k, (size, seconds) in enumerate(parts, 1):
        stem = "recap" if len(parts) == 1 else f"{name} - Part {k} (Chapter {k * 10 - 9} to Chapter {k * 10})"
        (d / "output" / f"{stem}.mp4").write_bytes(os.urandom(size))
        out.append({"video": f"output/{stem}.mp4", "srt": f"output/{stem}.srt", "chapters": "", "seconds": seconds})
    store.update(pid, lambda p: p.update(output={"video": out[0]["video"], "parts": out, "shorts": []}))
    return pid, d


def sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(pid: str) -> dict:
    return (json.loads((FILES / "posted.json").read_text(encoding="utf-8")) if (FILES / "posted.json").exists() else {}).get(pid, {})


def pending() -> dict:
    return json.loads((FILES / "pending.json").read_text(encoding="utf-8")) if (FILES / "pending.json").exists() else {}


# ---- the app's key and secret, and connecting ----
pid, d = project("Give My Regards to Black Jack", [(25 * MB, 540.0)])
store.update(pid, lambda p: p.update(youtube={"title": "I Made 2x My Salary in ONE Night… | Manga Recap"}))
r = requests.post(f"{B}/projects/{pid}/post", json={})
check("posting refused before connecting", r.status_code == 400 and "Connect" in r.json()["detail"], r.text)
r = requests.put(f"{B}/client", json={"client_key": "not a key!", "client_secret": "x"})
check("a wrong client key refused", r.status_code == 400, r.text)
r = requests.put(f"{B}/client", json={"client_key": "awtestclientkey1", "client_secret": "testsecret0123456789"})
check("key and secret saved on this PC, the secret not shown", r.ok and r.json()["client"] and "testsecret" not in r.text
      and json.loads((FILES / "client.json").read_text())["client_secret"] == "testsecret0123456789", r.text)
requests.post(f"{B}/connect")
q = query(opened[-1])
check("browser opened on TikTok's sign-in page (Login Kit for desktop)", opened[-1].startswith(f"{T}/v2/auth/authorize/?")
      and q["client_key"] == "awtestclientkey1" and q["scope"] == "user.info.basic,video.publish"
      and q["response_type"] == "code" and q["code_challenge_method"] == "S256"
      and re.fullmatch(r"http://127\.0\.0\.1:\d+/callback/", q["redirect_uri"])
      and re.fullmatch(r"[0-9a-f]{64}", q["code_challenge"]), q)
page = requests.get(q["redirect_uri"], params={"code": "good-code", "state": "someone-else"}).text
check("an answer for another sign-in is refused", "wasn't for this sign-in" in page, page[-200:])
requests.post(f"{B}/connect")
q = query(opened[-1])
SEEN.update(challenge=q["code_challenge"], redirect=q["redirect_uri"])
page = requests.get(q["redirect_uri"], params={"code": "good-code", "scopes": "user.info.basic,video.publish",
                                               "state": q["state"]}).text
check("signed in (the verifier checked against TikTok's hex challenge)", "Connected to Test Creator" in page
      and SEEN["verifier_ok"], page[-200:])
a = requests.get(f"{B}/account").json()
check("the account shown, with its @username", a["user"] and a["user"]["nickname"] == "Test Creator"
      and a["user"]["username"] == "testcreator" and a["connect"]["state"] == "done", a)
token = json.loads((FILES / "token.json").read_text())
check("its sign-in kept in the plugin's own folder", token["refresh_token"] == fake_tiktok.REFRESH["current"] == "rft.1"
      and token["open_id"] == "open-1", token)
check("who sees it has no default; not audited to start with", a["settings"]["privacy"] == "" and a["settings"]["audited"] is False
      and a["redirect"] == "http://127.0.0.1:*/callback/", a["settings"])

CONFIG["scope"] = "user.info.basic"
requests.post(f"{B}/connect")
q = query(opened[-1])
SEEN.update(challenge=q["code_challenge"], redirect=q["redirect_uri"])
page = requests.get(q["redirect_uri"], params={"code": "good-code", "state": q["state"]}).text
check("a sign-in without permission to post: said, and the account before kept", "permission to post videos" in page
      and "Direct Post" in page and requests.get(f"{B}/account").json()["user"]["nickname"] == "Test Creator", page[-300:])
CONFIG["scope"] = "user.info.basic,video.publish"
fake_tiktok.REFRESH["current"] = token["refresh_token"]  # (that sign-in's tokens weren't kept: the one before still works)

# ---- one video, while the app isn't audited ----
r = requests.put(f"{B}/projects/{pid}", json={"choice": {"privacy": "PUBLIC_TO_EVERYONE"}, "auto": "yes"})
check("choice saved, and Post it when its video is made switched on in its settings", r.ok and r.json()["auto"] == "yes"
      and store.load(pid)["settings"]["post-to-tiktok.auto"] == "yes", r.text)
check("a bad choice refused", requests.put(f"{B}/projects/{pid}", json={"choice": {"set": "everything"}}).status_code == 400
      and requests.put(f"{B}/projects/{pid}", json={"choice": {"privacy": "WORLD"}}).status_code == 400)
st = r.json()
check("what it posts: the whole video, titled as on YouTube", [(i["file"], i["state"]) for i in st["items"]]
      == [("recap.mp4", "new")] and st["items"][0]["title"] == "I Made 2x My Salary in ONE Night… | Manga Recap"
      and st["set"] == "video" and st["preview"] == "output/recap.mp4", st)
CONFIG["private"] = False
tt._after_job({"status": "done", "steps": ["panels", "video"], "project": pid})  # (as when Make the recap finishes)
job = finished(pid)
check("a public account, the app not audited: TikTok's refusal in plain words", job["status"] == "error"
      and "private account" in (job.get("error") or "") and "Settings and privacy" in job["error"], job)
check("... nothing kept as posted, and not tried again by itself", not records(pid) and pid not in pending())
CONFIG["private"] = True
CONFIG["fail_piece_once"] = SEEN["pieces"] + 2  # (the second piece fails once)
r = requests.post(f"{B}/projects/{pid}/post", json={})
job = finished(pid)
ups = fake_tiktok.posted()
check("posted", job["status"] == "done" and len(ups) == 1, job)
up = ups[-1]
info = up["post_info"]
check("whole and exact, in two pieces of TikTok's sizes, through a dropped one", up["sha"] == sha(d / "output" / "recap.mp4")
      and up["count"] == 2 and up["chunk"] == 10 * MB and SEEN["failed_once"] and not SEEN["bad"], (up["count"], SEEN["bad"]))
check("only you can see it while the app isn't audited, whatever the project chose", info["privacy_level"] == "SELF_ONLY", info)
check("its caption: the YouTube title and the series' hashtag",
      info["title"] == "I Made 2x My Salary in ONE Night… | Manga Recap\n\n#GiveMyRegardsToBlackJackRecap", info["title"])
check("comments, duets and stitches off until ticked; no labels", info["disable_comment"] is True and info["disable_duet"] is True
      and info["disable_stitch"] is True and info["is_aigc"] is False and info["brand_content_toggle"] is False
      and info["brand_organic_toggle"] is False, info)
check("the job says who sees it", "Posted 1 to TikTok (only you can see it)" in job["message"], job["message"])
rec = records(pid).get(f"recap.mp4|{25 * MB}") or {}
check("kept per piece, by its file's name and size, with TikTok's publish_id", rec.get("publish_id") == up["publish_id"]
      and rec.get("state") == "posted" and rec.get("privacy") == "SELF_ONLY", rec)
item = requests.get(f"{B}/projects/{pid}").json()["items"][0]
check("shown as posted, not public, with no address", item["state"] == "posted" and item["privacy"] == "SELF_ONLY"
      and not item["public"] and not item["url"], item)
requests.post(f"{B}/projects/{pid}/post", json={})
job = finished(pid)
check("nothing posted twice", len(fake_tiktok.posted()) == 1 and "Nothing new to post" in job["message"], job["message"])
log = requests.get(f"{B}/account").json()["log"]
check("Settings lists what it posted, and what it couldn't", log[0]["note"].startswith("I Made 2x") and "only you" in log[0]["note"]
      and "private account" in log[1]["note"], log[:2])

# ---- a sign-in that ran out: renewed, and TikTok's new refresh token kept ----
fake_tiktok.expire_access()
r = requests.get(f"{B}/creator")
check("renewed when it ran out, TikTok's new refresh token kept", r.ok and r.json()["nickname"] == "Test Creator"
      and SEEN["refreshes"] == 1 and json.loads((FILES / "token.json").read_text())["refresh_token"]
      == fake_tiktok.REFRESH["current"] == "rft.r1", r.text)
check("... and what the account can post asked fresh", r.json()["privacy"] == ["FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]
      and r.json()["longest"] == 600 and r.json()["public_account"] is False, r.json())

# ---- a series of three parts, from an audited app ----
sid, sd = project("Solo Leveling", [(6 * MB, 500.0), (3 * MB, 480.0), (11 * MB, 590.0)])
requests.put(f"{B}/settings", json={"settings": {"audited": True, "comments": True, "hashtags": "manga, #manhwa recap"}})
requests.post(f"{B}/projects/{sid}/post", json={})
job = finished(sid)
check("audited, and who sees it not chosen: asks first, posts nothing", job["status"] == "error"
      and "Choose who sees it" in (job.get("error") or "") and len(fake_tiktok.posted()) == 1, job)
CONFIG.update(private=False, audited=True, limit_inits=2)
requests.put(f"{B}/projects/{sid}", json={"choice": {"privacy": "PUBLIC_TO_EVERYONE"}})
inits, starts = len(SEEN["inits"]), len(SEEN["init_at"])
requests.post(f"{B}/projects/{sid}/post", json={})
job = finished(sid, 90)
ups = fake_tiktok.posted()[1:]
names = [f"Solo Leveling - Part {k} (Chapter {k * 10 - 9} to Chapter {k * 10}).mp4" for k in (1, 2, 3)]
check("three parts posted, in order", job["status"] == "done" and [u["sha"] for u in ups]
      == [sha(sd / "output" / n) for n in names], job)
check("each caption: Part n of 3, every part's hashtag, Settings' hashtags", [u["title"] for u in ups]
      == [f"Solo Leveling (Part {n} of 3)\n\nEvery part: #SoloLevelingRecap #manga #manhwa #recap" for n in (1, 2, 3)],
      [u["title"] for u in ups])
check("pieces of TikTok's sizes: under 5 MB and under 10 MB as one, 11 MB as one with the rest in it",
      [(u["chunk"], u["count"]) for u in ups] == [(6 * MB, 1), (3 * MB, 1), (10 * MB, 1)] and not SEEN["bad"], SEEN["bad"])
check("too many requests (429): waited out and asked again", SEEN["limited"] == 2 and len(SEEN["inits"]) - inits == 5,
      (SEEN["limited"], len(SEEN["inits"]) - inits))
at = SEEN["init_at"][starts:]
check("a pause between posts", len(at) == 3 and all(b - a >= tt.GAP for a, b in zip(at, at[1:])), at)
check("comments allowed as ticked, duets off as the account has them", all(
    u["post_info"]["disable_comment"] is False and u["post_info"]["disable_duet"] is True for u in ups))
st = requests.get(f"{B}/projects/{sid}").json()
check("asked to be public, but not called public until TikTok says so", all(
    i["state"] == "posted" and i["privacy"] == "PUBLIC_TO_EVERYONE" and not i["public"] and not i["url"] for i in st["items"])
    and "not public yet" in job["message"], (st["items"], job["message"]))
check("each part's record keeps its own publish_id", sorted(r["publish_id"] for r in records(sid).values())
      == sorted(u["publish_id"] for u in ups) and [r["n"] for r in records(sid).values()] == [1, 2, 3])
fake_tiktok.make_public()
tt.LOOK_AGAIN = 0
tt._check_public()
st = requests.get(f"{B}/projects/{sid}").json()
check("public once TikTok gives their public ids, with their addresses", all(
    i["public"] and re.fullmatch(r"https://www\.tiktok\.com/@testcreator/video/74\d+", i["url"]) for i in st["items"]), st["items"])

count = len(fake_tiktok.posted())
requests.post(f"{B}/projects/{sid}/post", json={})
job = finished(sid)
check("the series isn't posted twice", len(fake_tiktok.posted()) == count and "Nothing new" in job["message"], job["message"])
time.sleep(1.1)
(sd / "output" / names[1]).write_bytes(os.urandom(3 * MB + 5))  # (part 2 made again)
st = requests.get(f"{B}/projects/{sid}").json()
check("part 2 made again since it was posted", [i["state"] for i in st["items"]] == ["posted", "changed", "posted"], st["items"])
requests.post(f"{B}/projects/{sid}/post", json={})
job = finished(sid)
check("... not posted again unless asked", len(fake_tiktok.posted()) == count, job["message"])
requests.post(f"{B}/projects/{sid}/post", json={"again": True})
job = finished(sid)
check("... posted again when asked: that part only", len(fake_tiktok.posted()) == count + 1
      and fake_tiktok.posted()[-1]["title"].startswith("Solo Leveling (Part 2 of 3)"), job["message"])

# ---- a post TikTok was still processing when the job ended: looked up, not sent again ----
oid, od = project("Omniscient Reader", [(2 * MB, 300.0)])
requests.put(f"{B}/projects/{oid}", json={"choice": {"privacy": "MUTUAL_FOLLOW_FRIENDS"}})
CONFIG["polls"], tt.PROCESSING = 10 ** 6, 0.5
requests.post(f"{B}/projects/{oid}/post", json={})
job = finished(oid)
recs = records(oid)
check("still processing: said, and kept as sent", job["status"] == "done" and "still processing 1" in job["message"]
      and [r["state"] for r in recs.values()] == ["sending"], (job["message"], recs))
check("... shown as sent", requests.get(f"{B}/projects/{oid}").json()["items"][0]["state"] == "sending")
CONFIG["polls"] = 0
count = len(fake_tiktok.posted())
requests.post(f"{B}/projects/{oid}/post", json={})
job = finished(oid)
check("... looked up next time: posted, and not sent twice", len(fake_tiktok.posted()) == count
      and [r["state"] for r in records(oid).values()] == ["posted"] and "Nothing new" in job["message"], job["message"])
tt.PROCESSING = 900.0

# ---- a video too long for the account, and the Shorts ----
lid, ld = project("Tower of God", [(2 * MB, 1500.0)])
(ld / "output" / "shorts").mkdir()
shorts = []
for k in (1, 2):
    (ld / "output" / "shorts" / f"recap - Short {k:02}.mp4").write_bytes(os.urandom(400_000 + k))
    shorts.append(f"output/shorts/recap - Short {k:02}.mp4")
store.update(lid, lambda p: p["output"].update(shorts=shorts))
requests.put(f"{B}/projects/{lid}", json={"choice": {"privacy": "SELF_ONLY"}})
count = len(fake_tiktok.posted())
requests.post(f"{B}/projects/{lid}/post", json={})
job = finished(lid)
check("too long for the account: said, with what to post instead, nothing posted", job["status"] == "error"
      and "up to 10 minutes" in job["error"] and "runs 25 minutes" in job["error"] and "Shorts" in job["error"]
      and len(fake_tiktok.posted()) == count, job.get("error"))
st = requests.put(f"{B}/projects/{lid}", json={"choice": {"set": "shorts", "tag": "#Tower of God!"}}).json()
check("its Shorts as a series, with the hashtag chosen (cleaned)", st["set"] == "shorts" and st["sets"] == {"video": 1, "shorts": 2, "pieces": 0}
      and [i["caption"].splitlines()[0] for i in st["items"]] == ["Tower of God (Part 1 of 2)", "Tower of God (Part 2 of 2)"]
      and st["items"][0]["caption"].endswith("Every part: #TowerofGod #manga #manhwa #recap"), st["items"])

# ---- Advanced Cutting's pieces, when the video is made; TikTok's daily limit ----
cid, cd = project("Eleceed", [(3 * MB, 3000.0)])
pieces = cd / "output" / "Pieces"
pieces.mkdir()
names = [f"Eleceed - Part {k} (Ch.00{k} to Ch.00{k}).mp4" for k in (1, 2, 3)]
old = (cd / "output" / "recap.mp4").stat().st_mtime - 100
for n in names:
    (pieces / n).write_bytes(os.urandom(1 * MB))
    os.utime(pieces / n, (old, old))  # (the cut of the video before)
cutting = DATA / "plugin-data" / "advanced-cutting"
cutting.mkdir(parents=True, exist_ok=True)
(cutting / "made.json").write_text(json.dumps({cid: {str(pieces): [names[0], names[0][:-4] + ".srt", *names[1:]]}}), encoding="utf-8")
st = requests.put(f"{B}/projects/{cid}", json={"choice": {"set": "pieces", "privacy": "SELF_ONLY"}, "auto": "yes"}).json()
check("Advanced Cutting's pieces, in its order", [i["file"] for i in st["items"]] == names and st["sets"]["pieces"] == 3, st["items"])
before = pipeline.latest_for(cid)
tt._after_job({"status": "done", "steps": ["video"], "project": cid})
time.sleep(0.5)
check("when the video is made: waits for Advanced Cutting to cut it", cid in pending() and pipeline.latest_for(cid) is before)
tt._look()
time.sleep(0.5)
check("... pieces of the video before aren't posted", pipeline.latest_for(cid) is before and cid in pending())
for n in names:
    (pieces / n).write_bytes(os.urandom(1 * MB))  # (Advanced Cutting cut the new video)
CONFIG["daily_cap"] = len([p for p in fake_tiktok.posted() if p["state"] != "FAILED"]) + 1
count = len(fake_tiktok.posted())
tt._look()
job = finished(cid)
check("posted once they're cut; TikTok's daily limit stops it, saying when it carries on", job["status"] == "done"
      and len(fake_tiktok.posted()) == count + 1 and "daily limit" in job["message"] and "the rest go up after" in job["message"]
      and (FILES / "blocked.json").exists() and cid in pending(), job["message"])
first = pipeline.latest_for(cid)
tt._look()
time.sleep(0.5)
check("... nothing tried while it's used up", pipeline.latest_for(cid) is first)
CONFIG["daily_cap"] = None
(FILES / "blocked.json").unlink()  # (later)
tt._look()
job = finished(cid)
check("... the rest go up later, in order", job["status"] == "done" and [u["title"].splitlines()[0] for u in fake_tiktok.posted()[-3:]]
      == [f"Eleceed (Part {k} of 3)" for k in (1, 2, 3)] and cid not in pending(), job["message"])

# ---- disconnecting ----
a = requests.post(f"{B}/disconnect").json()
check("disconnected, and the access taken back", a["user"] is None and SEEN["revoked"] and not (FILES / "token.json").exists(), a)

# ---- on a server: the page opens TikTok's sign-in, and the address the browser couldn't open is pasted ----
tt.on_server = lambda: True
before = len(opened)
r = requests.post(f"{B}/connect").json()
check("on a server: the page opens the sign-in, not the server's own browser", r.get("server") is True
      and r["url"].startswith(f"{T}/v2/auth/authorize/") and len(opened) == before, r)
q = query(r["url"])
SEEN.update(challenge=q["code_challenge"], redirect=q["redirect_uri"])
r = requests.post(f"{B}/connect/paste", json={"address": "https://example.com/somewhere"})
check("... an address that isn't TikTok's answer: said", r.status_code == 400 and "127.0.0.1" in r.json()["detail"], r.text)
r = requests.post(f"{B}/connect/paste", json={"address": f"{q['redirect_uri']}?code=good-code&scopes=user.info.basic%2Cvideo.publish&state={q['state']}"})
a = requests.get(f"{B}/account").json()
check("... the right one pasted: connected", r.ok and a["user"] and a["user"]["nickname"] == "Test Creator", (r.text, a))
done()
