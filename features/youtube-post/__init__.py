"""Post to YouTube, a Recap Studio feature plugin: a project's video, and its Shorts if you like, posted to your channel
through YouTube's own API, when the video is made or when you say.

Google lets a program upload to a channel only through an OAuth client of its own, so each member makes one (a free
Google Cloud project with the YouTube Data API; Settings → YouTube says how) and connects their channel once. The
sign-in happens in the browser, on Google's own page, and comes back to a one-time address on this PC (127.0.0.1); only
the refresh token is kept, in token.json. Until that Google project passes YouTube's API audit, YouTube keeps the
videos uploaded through it private.

What's posted, per project:
- each part of the video, with the project's YouTube title ("(Part 2)" when there are parts), a description from the
  template with its YouTube chapter list, and its thumbnail;
- its Shorts, all at once or one a day at a time you choose (portrait projects only: YouTube takes a landscape video as
  an ordinary one).
A file posted once isn't posted again (posted.json), unless it was made again since and you ask. Posting runs as a job
("Posting to YouTube") with progress, Pause and Cancel; uploads go up in pieces and a broken one carries on where it
stopped. When YouTube's daily quota is used up (about 6 uploads a day for a Google project), the rest go up the next
day.

The Google addresses can be pointed elsewhere for tests (RECAP_YT_AUTH, RECAP_YT_TOKEN, RECAP_YT_API).
"""
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from glob import escape as glob_escape
from pathlib import Path

import requests
from fastapi import Body, HTTPException

from recap import pipeline, store

AUTH_URL = os.environ.get("RECAP_YT_AUTH") or "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = os.environ.get("RECAP_YT_TOKEN") or "https://oauth2.googleapis.com/token"
REVOKE_URL = TOKEN_URL.rsplit("/", 1)[0] + "/revoke"
API = os.environ.get("RECAP_YT_API") or "https://www.googleapis.com"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
MORE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"  # playlists and subtitles: manage your videos
CHUNK = 8 << 20                # an upload goes up in pieces this big (a multiple of 256 KB)
TRIES = 6                      # tries for a piece before giving up (2, 4, 8... seconds apart)
QUOTA = {"quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded", "dailyLimitExceeded"}
NO_API = "Turn on the YouTube Data API v3 in your Google Cloud project (APIs & Services → Library), then connect again"
FEWER = "Google wasn't given every permission: connect again and tick them all"
HINTS = {  # YouTube's reasons that need you to do something, in plain words
    "youtubeSignupRequired": "That Google account has no YouTube channel. Connect again and pick the account the channel "
                             "is on (a channel on a Brand Account: pick the Brand Account), or make the channel on youtube.com",
    "accessNotConfigured": NO_API, "SERVICE_DISABLED": NO_API,
    "insufficientPermissions": FEWER, "ACCESS_TOKEN_SCOPE_INSUFFICIENT": FEWER}
TITLE, DESCRIPTION = 100, 5000  # YouTube's limits (characters; the description's is in bytes)
DEFAULT_CHOICE = {"when": "off", "privacy": "private", "shorts": "none", "daily_at": "18:00", "title": "",
                  "description": "", "publish_at": "", "subtitles": False, "playlist": "", "playlist_shorts": False}
DEFAULT_SETTINGS = {"description": "{chapters}", "shorts_description": "{title}\n\n#Shorts", "category": "1",
                    "tags": "", "more": ""}  # more: "yes" asks for the wider permission (playlists, subtitles)
_lock = threading.Lock()
_session = requests.Session()
_plugin = None
_connect = {"state": "idle", "error": "", "url": ""}  # the sign-in under way


class PostError(Exception):
    """Something to show as it is."""


class QuotaError(PostError):
    """YouTube's daily quota is used up: the rest go up tomorrow."""


# ---- the plugin's own files ----------------------------------------------------------------------------------------

def _read(name: str, default):
    try:
        return json.loads((_plugin.data_dir / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(name: str, data) -> None:
    (_plugin.data_dir / name).write_text(json.dumps(data, indent=1), encoding="utf-8")


def _settings() -> dict:
    return {**DEFAULT_SETTINGS, **_read("settings.json", {})}


def _choice(pid: str) -> dict:
    return {**DEFAULT_CHOICE, **_read("projects.json", {}).get(pid, {})}


def _posted(pid: str) -> dict:
    return _read("posted.json", {}).get(pid, {})


def _record(pid: str, file: str, rec: dict) -> None:
    with _lock:
        posted = _read("posted.json", {})
        posted.setdefault(pid, {})[file] = rec
        _write("posted.json", posted)


# ---- signing in with Google (OAuth for desktop apps: a loopback address and PKCE) ----------------------------------

def _client() -> dict:
    c = _read("client.json", {})
    if not c.get("client_id") or not c.get("client_secret"):
        raise PostError("Add your Google OAuth client first (Settings → YouTube)")
    return c


def _token_request(data: dict) -> dict:
    r = requests.post(TOKEN_URL, data=data, timeout=30)
    try:
        found = r.json()
    except ValueError:
        found = {}
    if r.status_code != 200 or "access_token" not in found:
        raise PostError(f"Google didn't give a sign-in: {found.get('error_description') or found.get('error') or r.status_code}")
    return found


def _save_token(found: dict, keep_refresh: str = "") -> dict:
    token = _read("token.json", {})
    token.update(access_token=found["access_token"], expires=time.time() + int(found.get("expires_in", 3600)) - 60,
                 refresh_token=found.get("refresh_token") or keep_refresh or token.get("refresh_token", ""),
                 scope=found.get("scope") or token.get("scope", ""))
    _write("token.json", token)
    return token


def _access() -> str:
    """A working access token, refreshed when it's about to run out."""
    token = _read("token.json", {})
    if not token.get("refresh_token"):
        raise PostError("No YouTube channel is connected (Settings → YouTube)")
    if token.get("access_token") and time.time() < token.get("expires", 0):
        return token["access_token"]
    c = _client()
    try:
        found = _token_request({"client_id": c["client_id"], "client_secret": c["client_secret"],
                                "refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
    except PostError as e:
        raise PostError(f"{e}. Connect the channel again (Settings → YouTube).")
    return _save_token(found, token["refresh_token"])["access_token"]


def _auth() -> dict:
    return {"Authorization": f"Bearer {_access()}"}


def _channel() -> dict:
    r = _session.get(f"{API}/youtube/v3/channels", params={"part": "snippet", "mine": "true"}, headers=_auth(), timeout=30)
    _check_reply(r)
    items = r.json().get("items") or []
    if not items:
        raise PostError("That Google account has no YouTube channel yet")
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}


def start_connect() -> dict:
    """Open Google's sign-in page in the browser; its answer comes back to a one-time address on this PC."""
    c = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)

    class Answer(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if urllib.parse.urlparse(self.path).path != "/oauth":
                self.send_response(404)
                self.end_headers()
                return
            ok, text = False, ""
            try:
                if q.get("error"):
                    raise PostError(f"Google said: {q['error'][0]}")
                if q.get("state", [""])[0] != state or not q.get("code"):
                    raise PostError("That answer wasn't for this sign-in")
                found = _token_request({"client_id": c["client_id"], "client_secret": c["client_secret"],
                                        "code": q["code"][0], "code_verifier": verifier, "redirect_uri": redirect,
                                        "grant_type": "authorization_code"})
                if not found.get("refresh_token"):
                    raise PostError("Google gave no lasting sign-in: remove Recap Studio's access from your Google "
                                    "account (Security → Third-party access) and connect again")
                _save_token(found)
                token = _read("token.json", {})
                try:
                    token["channel"] = _channel()
                except (PostError, requests.RequestException):
                    (_plugin.data_dir / "token.json").unlink(missing_ok=True)  # (no channel: not connected after all)
                    raise
                _write("token.json", token)
                _connect.update(state="done", error="")
                ok, text = True, f"Connected to {token['channel']['title']}. You can close this tab and go back to Recap Studio."
            except (PostError, requests.RequestException) as e:
                _connect.update(state="error", error=str(e))
                text = f"Not connected: {e}"
            body = (f'<!doctype html><meta charset="utf-8"><title>Recap Studio</title><body style="font:16px system-ui;'
                    f'padding:32px"><h3>{"✓ " if ok else ""}Recap Studio</h3><p>{text}</p>').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            threading.Thread(target=server.shutdown, daemon=True).start()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Answer)
    redirect = f"http://127.0.0.1:{server.server_address[1]}/oauth"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPES + (" " + MORE_SCOPE if _settings()["more"] else ""), "include_granted_scopes": "true",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent"})

    def serve():
        timer = threading.Timer(600, server.shutdown)  # ten minutes to sign in
        timer.start()
        server.serve_forever()
        timer.cancel()
        server.server_close()
        if _connect["state"] == "waiting":
            _connect.update(state="error", error="The sign-in wasn't finished in ten minutes")

    _connect.update(state="waiting", error="", url=url)
    threading.Thread(target=serve, daemon=True, name="youtube-signin").start()
    webbrowser.open(url)
    return dict(_connect)


def disconnect() -> None:
    token = _read("token.json", {})
    if token.get("refresh_token"):
        try:
            requests.post(REVOKE_URL, params={"token": token["refresh_token"]}, timeout=15)
        except requests.RequestException:
            pass  # forgotten here either way
    (_plugin.data_dir / "token.json").unlink(missing_ok=True)


# ---- what a project posts --------------------------------------------------------------------------------------------

def _fit(title: str, suffix: str = "") -> str:
    title = re.sub(r"[<>]", "", title).strip()  # (YouTube refuses < and > in titles)
    room = TITLE - len(suffix)
    if len(title) > room:
        bar = title.rfind("|")
        if 0 < bar <= room and len(title) - bar <= 30:  # the "| Manga Recap" ending is what doesn't fit: left out
            title = title[:bar].rstrip(" -–—:|")
        else:  # after a whole word
            space = title.rfind(" ", 0, room)
            title = title[:space if space > 0 else room - 1].rstrip(" ,;:-–—|") + "…"
    return title + suffix


def _text(template: str, **fields) -> str:
    out = template
    for k, v in fields.items():
        out = out.replace("{" + k + "}", v)
    out = re.sub(r"[<>]", "", out).strip()
    while len(out.encode("utf-8")) > DESCRIPTION:
        out = out[:-50]
    return out


def plan(p: dict, choice: dict) -> list[dict]:
    """What the project posts, in order: each part of the video, then its Shorts (portrait projects)."""
    d = store.project_dir(p["id"])
    parts = [x for x in ((p.get("output") or {}).get("parts") or []) if (d / x["video"]).is_file()]
    title = (choice.get("title") or (p.get("youtube") or {}).get("title") or p["name"]).strip()
    s = _settings()
    out = []
    for n, part in enumerate(parts, 1):
        chapters = ""
        if part.get("chapters") and (d / part["chapters"]).is_file():
            chapters = (d / part["chapters"]).read_text(encoding="utf-8").strip()
        name = _fit(title, f" (Part {n})" if len(parts) > 1 else "")
        out.append({"file": part["video"], "kind": "video", "title": name, "thumbnail": True, "srt": part.get("srt"),
                    "description": _text(choice.get("description") or s["description"], title=name, series=p["name"],
                                         chapters=chapters, part=str(n))})
    if choice["shorts"] != "none" and p["settings"].get("orientation") == "portrait":
        k = 0
        for part in parts:
            video = d / part["video"]
            for f in sorted((video.parent / "shorts").glob(f"{glob_escape(video.stem)} - Short *.mp4")):
                k += 1
                name = _fit(title, f" (Part {k})")
                out.append({"file": f.relative_to(d).as_posix(), "kind": "short", "n": k, "title": name,
                            "description": _text(s["shorts_description"], title=name, series=p["name"], chapters="",
                                                 part=str(k))})
    return out


def _stamp(path: Path) -> list:
    st = path.stat()
    return [st.st_size, int(st.st_mtime)]


def _due(pid: str, item: dict, choice: dict, now: float) -> float:
    """When a Short posted one a day goes up: the first at the chosen time after the video went up, then a day apart."""
    if item["kind"] != "short" or choice["shorts"] != "daily":
        return 0.0
    first = _read("schedule.json", {}).get(pid)
    if not first:
        return float("inf")  # (the video hasn't gone up yet)
    return first + (item["n"] - 1) * 86400


def _next_at(hhmm: str, after: float) -> float:
    h, m = (int(x) for x in hhmm.split(":"))
    t = datetime.fromtimestamp(after).replace(hour=h, minute=m, second=0, microsecond=0)
    return (t if t.timestamp() > after else t + timedelta(days=1)).timestamp()


def status(pid: str) -> dict:
    """For the page: each thing the project posts, and where it stands."""
    p = store.load(pid)
    choice, posted, now = _choice(pid), _posted(pid), time.time()
    d = store.project_dir(pid)
    items = []
    for it in plan(p, choice):
        rec = posted.get(it["file"])
        due = _due(pid, it, choice, now)
        state = ("changed" if rec and rec.get("stamp") != _stamp(d / it["file"]) else "posted") if rec \
            else "due" if due and due != float("inf") and due > now else "waiting" if due == float("inf") else "new"
        items.append({"file": it["file"], "kind": it["kind"], "title": it["title"], "state": state,
                      "url": (rec or {}).get("url"), "at": (rec or {}).get("at"),
                      "subtitles": bool((rec or {}).get("subtitles")), "playlist": bool((rec or {}).get("playlist")),
                      "due": datetime.fromtimestamp(due).strftime("%d %b %H:%M") if state == "due" else None})
    token = _read("token.json", {})
    return {"choice": choice, "items": items, "portrait": p["settings"].get("orientation") == "portrait",
            "title": ((p.get("youtube") or {}).get("title") or p["name"]).strip(),
            "channel": (token.get("channel") or {}).get("title") if token.get("refresh_token") else None,
            "blocked_until": _blocked_until(), "settings": _settings(), "more": _more()}


def _blocked_until() -> str | None:
    until = _read("quota.json", {}).get("until", 0)
    return datetime.fromtimestamp(until).strftime("%d %b %H:%M") if until > time.time() else None


# ---- uploading ---------------------------------------------------------------------------------------------------------

def _check_reply(r: requests.Response) -> None:
    if r.status_code < 400:
        return
    try:
        err = r.json().get("error") or {}
    except ValueError:
        err = {}
    reasons = [e.get("reason") for e in (err.get("errors") or []) + (err.get("details") or []) if isinstance(e, dict)]
    message = err.get("message") or r.reason or str(r.status_code)
    if set(reasons) & QUOTA:
        raise QuotaError(f"YouTube's daily limit is used up ({message})")
    hint = next((HINTS[x] for x in reasons if x in HINTS), "")
    if hint:
        raise PostError(f"YouTube said: {message}. {hint}")
    raise PostError(f"YouTube said: {message}" + (f" ({reasons[0]})" if reasons and reasons[0] else ""))


def _wait(job, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        pipeline._check(job)
        time.sleep(0.2)


def _upload(job, path: Path, meta: dict, report) -> dict:
    """One video, in pieces (YouTube's resumable upload); a piece that fails is asked about and sent again."""
    size = path.stat().st_size
    r = _session.post(f"{API}/upload/youtube/v3/videos", params={"uploadType": "resumable", "part": "snippet,status"},
                      headers={**_auth(), "Content-Type": "application/json; charset=UTF-8",
                               "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"},
                      data=json.dumps(meta), timeout=60)
    _check_reply(r)
    session, sent, tries = r.headers["Location"], 0, 0
    with open(path, "rb") as f:
        while True:
            pipeline._check(job)
            f.seek(sent)
            piece = f.read(CHUNK)
            headers = {**_auth(), "Content-Range": f"bytes {sent}-{sent + len(piece) - 1}/{size}" if piece
                       else f"bytes */{size}"}
            try:
                r = _session.put(session, data=piece, headers=headers, timeout=600)
            except requests.RequestException:
                r = None
            if r is not None and r.status_code in (200, 201):
                return r.json()
            if r is not None and r.status_code == 308:
                got = re.search(r"bytes=\d+-(\d+)", r.headers.get("Range", ""))
                sent, tries = (int(got.group(1)) + 1 if got else 0), 0
                report(sent / size)
                continue
            if r is not None and r.status_code not in (401, 500, 502, 503, 504):
                _check_reply(r)
            tries += 1
            if tries >= TRIES:
                raise PostError("The upload kept failing: try again later")
            _wait(job, 2 ** tries)
            if r is not None and r.status_code == 401:
                _save_token({"access_token": "", "expires_in": 0})  # (refreshed on the next piece)
            try:  # where did it get to?
                q = _session.put(session, headers={**_auth(), "Content-Range": f"bytes */{size}"}, timeout=60)
                if q.status_code in (200, 201):
                    return q.json()
                got = re.search(r"bytes=\d+-(\d+)", q.headers.get("Range", "")) if q.status_code == 308 else None
                sent = int(got.group(1)) + 1 if got else 0 if q.status_code == 308 else sent
            except requests.RequestException:
                pass


def _thumbnail(video_id: str, p: dict) -> str:
    """Set the video's thumbnail; a note when YouTube won't take it (custom thumbnails need a verified channel)."""
    src = store.project_dir(p["id"]) / ((p.get("youtube") or {}).get("file") or "output/thumbnail.jpg")
    if not src.is_file():
        return "no thumbnail made yet"
    data = src.read_bytes()
    if len(data) > 2 << 20:  # YouTube takes up to 2 MB
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        data = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
    r = _session.post(f"{API}/upload/youtube/v3/thumbnails/set", params={"videoId": video_id, "uploadType": "media"},
                      headers={**_auth(), "Content-Type": "image/jpeg"}, data=data, timeout=120)
    try:
        _check_reply(r)
    except QuotaError:
        raise
    except PostError as e:
        return f"thumbnail not set: {e}"
    return ""


def _subtitles(video_id: str, srt: Path) -> None:
    """The video's subtitles (the .srt the app made), as its English captions."""
    boundary = secrets.token_hex(16)
    meta = json.dumps({"snippet": {"videoId": video_id, "language": "en", "name": "", "isDraft": False}})
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + srt.read_bytes() \
        + f"\r\n--{boundary}--\r\n".encode()
    r = _session.post(f"{API}/upload/youtube/v3/captions", params={"part": "snippet", "uploadType": "multipart"},
                      headers={**_auth(), "Content-Type": f"multipart/related; boundary={boundary}"}, data=body, timeout=120)
    _check_reply(r)


def playlists() -> list[dict]:
    """The channel's playlists: [{"id", "title"}]."""
    out, page = [], ""
    for _ in range(4):  # (up to 200)
        r = _session.get(f"{API}/youtube/v3/playlists", params={"part": "snippet", "mine": "true", "maxResults": 50,
                                                                 **({"pageToken": page} if page else {})},
                         headers=_auth(), timeout=30)
        _check_reply(r)
        found = r.json()
        out += [{"id": x["id"], "title": x["snippet"]["title"]} for x in found.get("items") or []]
        page = found.get("nextPageToken")
        if not page:
            break
    return out


def _playlist_for(p: dict, choice: dict) -> str:
    """The playlist the project's videos go in: the one chosen, or the one named after the series (made the first time,
    and remembered)."""
    if choice["playlist"] != "new":
        return choice["playlist"]
    title = _fit(p["name"])
    found = next((x["id"] for x in playlists() if x["title"] == title), None)
    if not found:
        privacy = choice["privacy"] if choice["privacy"] in ("public", "unlisted") else "private"
        r = _session.post(f"{API}/youtube/v3/playlists", params={"part": "snippet,status"}, headers=_auth(),
                          json={"snippet": {"title": title}, "status": {"privacyStatus": privacy}}, timeout=30)
        _check_reply(r)
        found = r.json()["id"]
    with _lock:
        choices = _read("projects.json", {})
        choices.setdefault(p["id"], {})["playlist"] = found
        _write("projects.json", choices)
    choice["playlist"] = found
    return found


def _add_to_playlist(video_id: str, playlist_id: str) -> None:
    r = _session.post(f"{API}/youtube/v3/playlistItems", params={"part": "snippet"}, headers=_auth(),
                      json={"snippet": {"playlistId": playlist_id,
                                        "resourceId": {"kind": "youtube#video", "videoId": video_id}}}, timeout=30)
    _check_reply(r)


def _more() -> bool:
    """Whether the channel was connected with the wider permission playlists and subtitles need."""
    return MORE_SCOPE in (_read("token.json", {}).get("scope") or "").split()


def _stop_for_quota(pid: str) -> str:
    tomorrow = datetime.now(timezone.utc).replace(hour=8, minute=5, second=0, microsecond=0)  # midnight Pacific
    if tomorrow.timestamp() <= time.time():
        tomorrow += timedelta(days=1)
    _write("quota.json", {"until": tomorrow.timestamp()})
    _set_pending(pid, True)
    return f"YouTube's daily limit is used up: the rest go up after {_blocked_until()}"


def post(job) -> None:
    """The job step: post what the project hasn't posted yet (and what's due), then its subtitles and playlist, for
    what's posted without them."""
    pid = job["project"]
    p, choice, now = store.load(pid), _choice(pid), time.time()
    if not _read("token.json", {}).get("refresh_token"):
        raise PostError("No YouTube channel is connected (Settings → YouTube)")
    if _read("quota.json", {}).get("until", 0) > now:
        job["note"] = f"YouTube's daily limit is used up: the rest go up after {_blocked_until()}"
        return
    again = bool((job.get("options") or {}).get("again"))
    d, posted, s = store.project_dir(pid), _posted(pid), _settings()
    wants = {"subtitles": bool(choice["subtitles"]), "playlist": bool(choice["playlist"])}
    more = _more()
    notes, n, extras = [], 0, 0
    if any(wants.values()) and not more:
        notes.append("subtitles and playlists need the wider permission: Settings → YouTube → Connect again")
    items = []
    for it in plan(p, choice):
        rec = posted.get(it["file"])
        upload = not rec or (again and rec.get("stamp") != _stamp(d / it["file"]))
        if upload and _due(pid, it, choice, now) > now:
            continue
        items.append((it, upload))
    sizes = [(d / it["file"]).stat().st_size if upload else 0 for it, upload in items]
    done_bytes = 0
    try:
        for (it, upload), size in zip(items, sizes):
            rec = posted.get(it["file"])
            if upload:
                def report(fraction, it=it, base=done_bytes, size=size):
                    job["progress"] = min(0.99, (base + fraction * size) / max(1, sum(sizes)))
                    job["message"] = f"Posting to YouTube: {it['title']} ({round(fraction * 100)}%)"
                report(0)
                privacy = choice["privacy"] if choice["privacy"] in ("private", "unlisted", "public") else "private"
                status_ = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
                if it["kind"] == "video" and choice.get("publish_at") and privacy == "private":
                    at = datetime.fromisoformat(choice["publish_at"]).astimezone(timezone.utc)
                    if at.timestamp() > now:
                        status_["publishAt"] = at.strftime("%Y-%m-%dT%H:%M:%SZ")
                meta = {"snippet": {"title": it["title"], "description": it["description"], "categoryId": s["category"],
                                    "tags": [t.strip() for t in s["tags"].split(",") if t.strip()][:30]},
                        "status": status_}
                vid = _upload(job, d / it["file"], meta, report)["id"]
                rec = {"video_id": vid, "stamp": _stamp(d / it["file"]), "kind": it["kind"], "title": it["title"],
                       "url": f"https://youtu.be/{vid}" if it["kind"] == "video" else f"https://youtube.com/shorts/{vid}",
                       "at": datetime.now().strftime("%d %b %H:%M"), "privacy": privacy}
                _record(pid, it["file"], rec)
                n += 1
                done_bytes += size
                if it.get("thumbnail"):
                    note = _thumbnail(vid, p)
                    if note:
                        notes.append(note)
                if it["kind"] == "video" and choice["shorts"] == "daily" and not _read("schedule.json", {}).get(pid):
                    schedule = _read("schedule.json", {})
                    schedule[pid] = _next_at(choice["daily_at"], time.time())
                    _write("schedule.json", schedule)
            if not more:
                continue
            vid = rec["video_id"]
            if (wants["subtitles"] and it["kind"] == "video" and not rec.get("subtitles")
                    and it.get("srt") and (d / it["srt"]).is_file()):
                job["message"] = f"Posting to YouTube: the subtitles of {it['title']}"
                try:
                    _subtitles(vid, d / it["srt"])
                    rec["subtitles"] = True
                    _record(pid, it["file"], rec)
                    extras += 1
                except QuotaError:
                    raise
                except PostError as e:
                    notes.append(f"subtitles not added: {e}")
            if wants["playlist"] and (it["kind"] == "video" or choice["playlist_shorts"]):
                job["message"] = f"Posting to YouTube: adding {it['title']} to the playlist"
                try:
                    playlist = _playlist_for(p, choice)
                    if rec.get("playlist") != playlist:
                        _add_to_playlist(vid, playlist)
                        rec["playlist"] = playlist
                        _record(pid, it["file"], rec)
                        extras += 1
                except QuotaError:
                    raise
                except PostError as e:
                    notes.append(f"not added to the playlist: {e}")
    except QuotaError:
        notes.append(_stop_for_quota(pid))
    else:
        _set_pending(pid, False)  # (everything that was due went up)
    if not n and not extras and not notes:
        job["note"] = "Nothing new to post"
        return
    what = [x for x in (f"posted {n} to YouTube" if n else "",
                        f"{extras} subtitles or playlist additions" if extras else "") if x]
    note = "; ".join(what) or "nothing posted"
    job["note"] = note[:1].upper() + note[1:] + (f" ({'; '.join(notes)})" if notes else "")


# ---- when it posts by itself ---------------------------------------------------------------------------------------

def _after_job(job: dict) -> None:
    """When a project's video is made: post it, if the project says so."""
    if job["status"] != "done" or "video" not in job["steps"]:
        return
    pid = job["project"]
    if _choice(pid)["when"] == "auto" and _read("token.json", {}).get("refresh_token"):
        try:
            _plugin.run(pid, "post")
        except Exception as e:  # (a job started meanwhile: the next look tries again)
            print("Post to YouTube:", e)


def _look() -> None:
    """Every minute: the Shorts that are due (one a day), and the posting that stopped at the daily quota. Nothing
    else goes up by itself here: a video only when it's made (_after_job), or when you say."""
    if not _read("token.json", {}).get("refresh_token") or _read("quota.json", {}).get("until", 0) > time.time():
        return
    pending, schedule, now = _read("pending.json", {}), _read("schedule.json", {}), time.time()
    for pid in sorted(set(pending) | set(schedule)):
        try:
            p = store.load(pid)
        except Exception:
            continue
        choice, mine = _choice(pid), _posted(pid)
        due = [it for it in plan(p, choice) if it["file"] not in mine and _due(pid, it, choice, now) <= now]
        if pid not in pending:
            due = [it for it in due if it["kind"] == "short"]
        if not due:
            if pid in pending:
                _set_pending(pid, False)
            continue
        job = _plugin.job(pid)
        if job and job["status"] in pipeline.ACTIVE:
            continue
        try:
            _plugin.run(pid, "post")
        except Exception as e:
            print("Post to YouTube:", e)


def _set_pending(pid: str, on: bool) -> None:
    """A project whose posting stopped at the daily quota (carried on by _look the next day)."""
    with _lock:
        pending = _read("pending.json", {})
        if on:
            pending[pid] = True
        else:
            pending.pop(pid, None)
        _write("pending.json", pending)


# ---- the plugin's API --------------------------------------------------------------------------------------------------

def setup(plugin):
    global _plugin
    _plugin = plugin
    plugin.step("post", "Posting to YouTube", post)
    plugin.on("job_done", _after_job)
    plugin.every(60, _look)

    def safely(fn, *args):
        try:
            return fn(*args)
        except PostError as e:
            raise HTTPException(400, str(e))
        except requests.RequestException as e:
            raise HTTPException(502, f"Google didn't answer: {e}")

    @plugin.router.get("/account")
    def account():
        c, token = _read("client.json", {}), _read("token.json", {})
        return {"client": bool(c.get("client_id") and c.get("client_secret")),
                "client_id": c.get("client_id", "")[:24] + ("…" if len(c.get("client_id", "")) > 24 else ""),
                "channel": (token.get("channel") or {}).get("title") if token.get("refresh_token") else None,
                "connect": {k: _connect[k] for k in ("state", "error")}, "blocked_until": _blocked_until(),
                "settings": _settings(), "more": _more()}

    @plugin.router.put("/client")
    def set_client(client_id: str = Body(..., embed=True), client_secret: str = Body(..., embed=True)):
        client_id, client_secret = client_id.strip(), client_secret.strip()
        if not client_id.endswith(".apps.googleusercontent.com") or not client_secret:
            raise HTTPException(400, "That isn't a Google OAuth client (its ID ends in .apps.googleusercontent.com)")
        _write("client.json", {"client_id": client_id, "client_secret": client_secret})
        return account()

    @plugin.router.put("/settings")
    def set_settings(settings: dict = Body(..., embed=True)):
        clean = {k: str(settings[k]) for k in DEFAULT_SETTINGS if k in settings}
        _write("settings.json", {**_settings(), **clean})
        return account()

    @plugin.router.post("/connect")
    def connect():
        return safely(start_connect)

    @plugin.router.post("/disconnect")
    def drop():
        disconnect()
        return account()

    @plugin.router.get("/playlists")
    def channel_playlists():
        return {"playlists": safely(playlists)}

    @plugin.router.get("/projects/{pid}")
    def project_status(pid: str):
        try:
            return status(pid)
        except KeyError:
            raise HTTPException(404, "No such project")

    @plugin.router.put("/projects/{pid}")
    def set_choice(pid: str, choice: dict = Body(..., embed=True)):
        store.load(pid)
        clean = {k: choice[k] for k in DEFAULT_CHOICE if k in choice}
        for k, allowed in (("when", ("off", "auto")), ("privacy", ("private", "unlisted", "public")),
                           ("shorts", ("none", "all", "daily"))):
            if k in clean and clean[k] not in allowed:
                raise HTTPException(400, f"{k} can't be {clean[k]}")
        if clean.get("daily_at") and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", clean["daily_at"]):
            raise HTTPException(400, "The time for Shorts is hh:mm")
        if "playlist" in clean and not re.fullmatch(r"|new|[\w-]{2,64}", str(clean["playlist"])):
            raise HTTPException(400, "That isn't a playlist")
        for k in ("subtitles", "playlist_shorts"):
            if k in clean:
                clean[k] = bool(clean[k])
        with _lock:
            choices = _read("projects.json", {})
            choices[pid] = {**DEFAULT_CHOICE, **choices.get(pid, {}), **clean}
            _write("projects.json", choices)
        return status(pid)

    @plugin.router.post("/projects/{pid}/post")
    def post_now(pid: str, again: bool = Body(False, embed=True)):
        store.load(pid)
        if not _read("token.json", {}).get("refresh_token"):
            raise HTTPException(400, "Connect your YouTube channel first (Settings → YouTube)")
        job = pipeline.latest_for(pid)
        if job and job["status"] in pipeline.ACTIVE:
            raise HTTPException(409, "A job is already running for this project")
        return pipeline.public(pipeline.submit(pid, [f"{plugin.id}.post"], options={"again": again}))
