"""Post to TikTok, a Recap Studio feature plugin: a project's finished video posted to your TikTok account through
TikTok's own API (the Content Posting API's direct post), when the video is made or when you say. A video in several
pieces (the app's parts, its Shorts, or Advanced Cutting's pieces) goes up as a numbered series, in order.

TikTok lets a program post only through an app of its own on TikTok for Developers, so each member registers one
(Login Kit for desktop, and the Content Posting API with Direct Post on) and pastes its client key and secret into
Settings → Post to TikTok. The sign-in happens in the browser, on TikTok's own page (OAuth 2's authorization code with
PKCE; TikTok's desktop flow takes the challenge as the verifier's SHA-256 in hex), and comes back to a one-time address
on this PC, http://127.0.0.1:<port>/callback/ (registered in the app as http://127.0.0.1:*/callback/). The tokens are
kept in token.json. TikTok gives a new refresh token each time the sign-in is renewed, so the newest is always kept.
On a server (Recap Studio 0.4's --server), the address the member's browser couldn't open is pasted into Settings
instead (POST /connect/paste), as Post to YouTube does.

TikTok's rules, as its documentation gave them in November 2026:
- Until TikTok audits the member's app, it posts only as SELF_ONLY (only the account sees it), only to a private
  account, and for up to 5 accounts a day. Settings says whether the app has passed; until it has, every post is
  SELF_ONLY. A post is only shown as public once TikTok gives its public id (publicaly_available_post_id, given once a
  public post has passed TikTok's checks), looked for again for two days (_check_public).
- Before posting, the account's options are asked (creator_info): who may see it, whether comments, duets and
  stitches can be allowed, and the longest video it may post (often 10 minutes). Nothing is chosen for the member:
  who sees it has no default, and comments, duets and stitches are off until they're ticked.
- 6 posts a minute per sign-in, and about 15 a day per account across every app: a pause between posts (GAP), a
  retry after "too many requests" (429, BACKOFF), and when the day's posts are used up the rest go up later
  (blocked.json and pending.json, carried on by _look).
- TikTok's API has no comments, playlists or links. "Linked" here: each caption says "Part 2 of 5" and carries the
  series' hashtag ("Every part: #SoloLevelingRecap"), so tapping it lists the parts.

Each piece is posted once: posted.json keeps a record per piece, by its file's name and size, with TikTok's
publish_id (and its public id once there is one). A post sent but not settled (the app closed, or the job was
cancelled while TikTok processed it) is looked up before anything else is sent.

The TikTok addresses can be pointed elsewhere for tests (RECAP_TT_AUTH, RECAP_TT_API).
"""
import hashlib
import http.server
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from glob import escape as glob_escape
from pathlib import Path

import requests
from fastapi import Body, HTTPException

from recap import pipeline, store

AUTH_URL = os.environ.get("RECAP_TT_AUTH") or "https://www.tiktok.com/v2/auth/authorize/"
API = (os.environ.get("RECAP_TT_API") or "https://open.tiktokapis.com").rstrip("/")
TOKEN_URL = f"{API}/v2/oauth/token/"
REVOKE_URL = f"{API}/v2/oauth/revoke/"
SCOPES = "user.info.basic,video.publish"  # (TikTok's scopes are comma-separated)
REDIRECT = "http://127.0.0.1:*/callback/"  # what the member registers in Login Kit (any port)
CHUNK = 10 << 20             # a video goes up in pieces this big (TikTok: 5 to 64 MB; the last one takes the rest)
LARGEST = 4_000_000_000      # TikTok takes videos up to 4 GB
TRIES = 6                    # tries for a request or a piece before giving up
GAP = 30.0                   # seconds between two posts
BACKOFF = 5.0                # the first wait after "too many requests" or a piece that failed, doubling each time
POLL = 5.0                   # seconds between looks at a post TikTok is processing (it allows 30 a minute)
PROCESSING = 900.0           # how long a post's processing is waited for before the job moves on
LOOK_AGAIN = 600.0           # seconds between looks for a public post's public id
CAPTION = 2200               # TikTok's longest caption (in UTF-16 units)
BLOCK = 3 * 3600             # after one of TikTok's daily limits, the next try this long after
WAIT_PIECES = 12 * 3600      # how long "post it when the video is made" waits for Advanced Cutting's pieces
LOG = 40
MUSIC = "https://www.tiktok.com/legal/page/global/music-usage-confirmation/en"
BRANDED = "https://www.tiktok.com/legal/page/global/bc-policy/en"
PRIVACY = {"SELF_ONLY": "Only you", "MUTUAL_FOLLOW_FRIENDS": "Friends (followers you follow back)",
           "FOLLOWER_OF_CREATOR": "Your followers", "PUBLIC_TO_EVERYONE": "Everyone"}
SETS = ("video", "shorts", "pieces")
DISCLOSURE = ("none", "yours", "branded", "both")
DEFAULT_SETTINGS = {"audited": False, "privacy": "", "set": "video", "comments": False, "duet": False,
                    "stitch": False, "aigc": False, "disclosure": "none", "hashtags": ""}
DEFAULT_CHOICE = {"set": "", "privacy": "", "title": "", "description": "", "tag": ""}
WHY = {"video": "No video yet: Make the recap renders it",
       "shorts": "No Shorts yet: set Cut into Shorts in the Video stage's panel (Captions and Shorts) and make the video again",
       "pieces": "No pieces yet: Advanced Cutting makes them (Cut the video… at the Video stage)"}
HINTS = {  # TikTok's reasons that need you to do something, in plain words
    "unaudited_client_can_only_post_to_private_accounts":
        "Until TikTok audits your app, it only takes posts to a private account: in TikTok, Settings and privacy → "
        "Privacy → Private account, then post again",
    "privacy_level_option_mismatch":
        "TikTok doesn't offer that choice of who sees it to this account now: choose again (Post to TikTok… at "
        "Publish)",
    "spam_risk_user_banned_from_posting": "TikTok isn't letting this account post for now",
    "scope_not_authorized":
        "TikTok wasn't given permission to post: connect again and allow it. On TikTok for Developers, your app needs "
        "the Content Posting API with Direct Post switched on",
    "access_token_invalid": "TikTok's sign-in ran out: connect again (Settings → Post to TikTok)",
}
LIMITS = {  # TikTok's daily limits: the rest go up later
    "spam_risk_too_many_posts": "TikTok's daily limit of posts from apps is used up for this account (about 15 a day)",
    "reached_active_user_cap": "TikTok's daily limit of accounts posting through your app is used up",
}
FAILS = {  # why TikTok didn't post a video it was sent (its fail_reason)
    "file_format_check_failed": "TikTok doesn't take this kind of video file",
    "duration_check_failed": "it's longer (or shorter) than TikTok takes from this account",
    "frame_rate_check_failed": "TikTok takes 23 to 60 frames a second",
    "picture_size_check_failed": "TikTok takes pictures 360 to 4096 pixels on each side",
    "internal": "TikTok had a problem on its side: post it again",
    "auth_removed": "Recap Studio's access was taken back in TikTok: connect again",
    "spam_risk_user_banned_from_posting": "TikTok isn't letting this account post for now",
    "spam_risk_text": "TikTok flagged its caption: change the title or description, then post again",
    "spam_risk": "TikTok flagged the post",
    "publish_cancelled": "it was cancelled",
}
_lock = threading.Lock()
_token_lock = threading.Lock()  # (one renewal at a time: each one makes the refresh token before it useless)
_session = requests.Session()
_plugin = None
_key = "post-to-tiktok.auto"
_connect = {"state": "idle", "error": "", "url": ""}  # the sign-in under way
_pending: dict = {}                                   # its state, verifier and one-time address
_lengths: dict = {}                                   # (file, size, time) -> seconds


class PostError(Exception):
    """Something to show as it is (code: TikTok's, when it gave one)."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


class LimitError(PostError):
    """One of TikTok's daily limits is used up: the rest go up later."""


# ---- the plugin's own files ----------------------------------------------------------------------------------------

def _read_json(path):
    """A JSON file, whole, or None when there's none. A read that lands as _write_json swaps the file in (refused
    for a moment on Windows) is tried again, rather than taken for no file."""
    for attempt in range(10):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            time.sleep(0.02 * (attempt + 1))
    return None


def _write_json(path, data) -> None:
    """Written whole or not at all: to a file of its own beside it, then swapped in, as the app's store does, so a
    read at the same moment never gets half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    for attempt in range(20):  # (Windows refuses the swap while something has the file open for a moment)
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


def _read(name: str, default):
    found = _read_json(_plugin.data_dir / name)
    return default if found is None else found


def _write(name: str, data) -> None:
    _write_json(_plugin.data_dir / name, data)


def _settings() -> dict:
    return {**DEFAULT_SETTINGS, **_read("settings.json", {})}


def _choice(pid: str) -> dict:
    return {**DEFAULT_CHOICE, **_read("projects.json", {}).get(pid, {})}


def _posted(pid: str) -> dict:
    return _read("posted.json", {}).get(pid, {})


def _record(pid: str, key: str, rec: dict) -> None:
    with _lock:
        posted = _read("posted.json", {})
        posted.setdefault(pid, {})[key] = rec
        _write("posted.json", posted)


def _forget(pid: str, key: str) -> None:
    with _lock:
        posted = _read("posted.json", {})
        if (posted.get(pid) or {}).pop(key, None) is not None:
            _write("posted.json", posted)


def _clock(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%d %b %H:%M")


def _log(project: str, note: str) -> None:
    with _lock:
        log = _read("log.json", [])
        log.insert(0, {"at": time.time(), "project": project, "note": note})
        _write("log.json", log[:LOG])


def _set_pending(pid: str, on: bool, since: float | None = None) -> None:
    """A project with posting still to do: its video was just made, or one of TikTok's daily limits stopped it
    (carried on by _look)."""
    with _lock:
        pending = _read("pending.json", {})
        if on:
            pending[pid] = {"since": since or (pending.get(pid) or {}).get("since") or time.time()}
        elif pid in pending:
            pending.pop(pid)
        else:
            return
        _write("pending.json", pending)


def _blocked() -> dict | None:
    b = _read("blocked.json", {})
    if b.get("until", 0) > time.time():
        return {"why": b.get("why", ""), "until": _clock(b["until"])}
    return None


def _block(why: str) -> None:
    _write("blocked.json", {"until": time.time() + BLOCK, "why": why})


# ---- signing in with TikTok (Login Kit for desktop: a loopback address and PKCE) -----------------------------------

def _client() -> dict:
    c = _read("client.json", {})
    if not c.get("client_key") or not c.get("client_secret"):
        raise PostError("Add your TikTok app's client key and secret first (Settings → Post to TikTok)")
    return c


def _token_request(data: dict) -> dict:
    try:
        r = requests.post(TOKEN_URL, data=data, timeout=30)
    except requests.RequestException as e:
        raise PostError(f"TikTok didn't answer: {e}")
    try:
        found = r.json()
    except ValueError:
        found = {}
    if r.status_code != 200 or not found.get("access_token"):
        raise PostError(f"TikTok didn't give a sign-in: {found.get('error_description') or found.get('error') or r.status_code}",
                        found.get("error") or "")
    return found


def _save_token(found: dict) -> dict:
    token, now = _read("token.json", {}), time.time()
    token.update(access_token=found["access_token"], expires=now + int(found.get("expires_in") or 7200) - 60,
                 refresh_token=found.get("refresh_token") or token.get("refresh_token", ""),
                 refresh_expires=now + int(found["refresh_expires_in"]) if found.get("refresh_expires_in")
                 else token.get("refresh_expires", 0),
                 open_id=found.get("open_id") or token.get("open_id", ""), scope=found.get("scope") or token.get("scope", ""))
    _write("token.json", token)
    return token


def _connected() -> bool:
    return bool(_read("token.json", {}).get("refresh_token"))


def _access(fresh: bool = False) -> str:
    """A working access token (TikTok's last 2 hours), renewed when it's about to run out, or when fresh."""
    with _token_lock:
        token = _read("token.json", {})
        if not token.get("refresh_token"):
            raise PostError("No TikTok account is connected (Settings → Post to TikTok)")
        if not fresh and token.get("access_token") and time.time() < token.get("expires", 0):
            return token["access_token"]
        c = _client()
        try:
            found = _token_request({"client_key": c["client_key"], "client_secret": c["client_secret"],
                                    "grant_type": "refresh_token", "refresh_token": token["refresh_token"]})
        except PostError as e:
            raise PostError(f"{e}. Connect your TikTok account again (Settings → Post to TikTok).", e.code)
        return _save_token(found)["access_token"]


def _user() -> dict | None:
    token = _read("token.json", {})
    return token.get("user") if token.get("refresh_token") else None


def _remember_user(info: dict) -> None:
    """The account's name and its @username, from TikTok's answer about what it can post (the username makes a
    public post's address)."""
    with _token_lock:
        token = _read("token.json", {})
        if not token.get("refresh_token"):
            return
        user = token.get("user") or {}
        user.update({k: info[k] for k in ("nickname", "username") if info.get(k)})
        token["user"] = user
        _write("token.json", token)


def on_server() -> bool:
    """Whether Recap Studio runs as a server (0.4 and later): TikTok's answer then can't reach it by itself."""
    try:
        from recap import system
    except ImportError:
        return False
    return bool(getattr(system, "SERVER", False))


def finish_connect(query: dict) -> str:
    """TikTok's answer (the one-time address's query, parsed): the tokens kept, and the account looked up. Returns
    what to tell the member; raises PostError when it didn't work."""
    c = _client()
    try:
        if query.get("error"):
            raise PostError(f"TikTok said: {(query.get('error_description') or query['error'])[0]}")
        if not _pending or query.get("state", [""])[0] != _pending["state"] or not query.get("code"):
            raise PostError("That answer wasn't for this sign-in: press Connect again")
        found = _token_request({"client_key": c["client_key"], "client_secret": c["client_secret"],
                                "code": query["code"][0], "grant_type": "authorization_code",
                                "redirect_uri": _pending["redirect"], "code_verifier": _pending["verifier"]})
        if "video.publish" not in re.split(r"[,\s]+", found.get("scope") or ""):
            raise PostError("TikTok wasn't given permission to post videos: connect again and allow it. On TikTok for "
                            "Developers, your app needs the Content Posting API with Direct Post switched on")
        if not found.get("refresh_token"):
            raise PostError("TikTok gave no lasting sign-in: connect again")
        (_plugin.data_dir / "token.json").unlink(missing_ok=True)  # (another account's name isn't kept)
        _save_token(found)
        name = ""
        try:
            got = _call("/v2/user/info/", method="GET", params={"fields": "open_id,display_name,avatar_url"})
            name = ((got.get("user") or {}).get("display_name") or "").strip()
        except PostError:
            pass  # (connected all the same: the name is looked up again before posting)
        token = _read("token.json", {})
        token["user"] = {"name": name, "nickname": name, "username": ""}
        _write("token.json", token)
        try:
            _remember_user(creator_info())
        except PostError:
            pass
        _connect.update(state="done", error="")
        _pending.clear()
        user = _user() or {}
        return (f"Connected to {user.get('nickname') or user.get('name') or 'your TikTok account'}. You can close this "
                f"tab and go back to Recap Studio.")
    except PostError as e:
        _connect.update(state="error", error=str(e))
        raise


def paste_answer(address: str) -> dict:
    """A server's sign-in: the address the member's browser couldn't open (TikTok's answer), pasted."""
    parsed = urllib.parse.urlparse((address or "").strip())
    if parsed.path.rstrip("/") != "/callback" or not parsed.query:
        raise PostError("That isn't the address TikTok sent you to: it starts with http://127.0.0.1, goes on with "
                        "/callback/, and has code= in it")
    listener = _pending.get("server")
    finish_connect(urllib.parse.parse_qs(parsed.query))
    if listener:  # (TikTok's answer came this way: the one-time address isn't waited on any more)
        threading.Thread(target=listener.shutdown, daemon=True).start()
    return dict(_connect)


def start_connect() -> dict:
    """Open TikTok's sign-in page in the browser; its answer comes back to a one-time address on this PC. On a
    server, the page opens it (its "url") in the member's own browser, and the answer is pasted (paste_answer)."""
    c = _client()
    verifier = secrets.token_urlsafe(64)  # (43 to 128 of A-Z, a-z, 0-9, - and _)
    challenge = hashlib.sha256(verifier.encode()).hexdigest()  # (TikTok's desktop flow: hex, not base64)
    state = secrets.token_urlsafe(24)

    class Answer(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path.rstrip("/") != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            ok = False
            try:
                text, ok = finish_connect(urllib.parse.parse_qs(url.query)), True
            except PostError as e:
                text = f"Not connected: {e}"
            text = text.replace("&", "&amp;").replace("<", "&lt;")
            body = (f'<!doctype html><meta charset="utf-8"><title>Recap Studio</title><body style="font:16px system-ui;'
                    f'padding:32px"><h3>{"✓ " if ok else ""}Recap Studio</h3><p>{text}</p>').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            threading.Thread(target=server.shutdown, daemon=True).start()

        def log_message(self, *args):
            pass

    old = _pending.get("server")
    if old:  # (a sign-in before this one isn't waited on any more)
        threading.Thread(target=old.shutdown, daemon=True).start()
    server = http.server.HTTPServer(("127.0.0.1", 0), Answer)
    redirect = f"http://127.0.0.1:{server.server_address[1]}/callback/"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_key": c["client_key"], "response_type": "code", "scope": SCOPES, "redirect_uri": redirect,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})

    def serve():
        timer = threading.Timer(600, server.shutdown)  # ten minutes to sign in
        timer.start()
        server.serve_forever()
        timer.cancel()
        server.server_close()
        if _connect["state"] == "waiting" and _pending.get("server") is server:
            _connect.update(state="error", error="The sign-in wasn't finished in ten minutes")

    _pending.clear()
    _pending.update(state=state, verifier=verifier, redirect=redirect, server=server)
    _connect.update(state="waiting", error="", url=url)
    threading.Thread(target=serve, daemon=True, name="tiktok-signin").start()
    if on_server():
        return {**_connect, "server": True}  # (the page opens it: the server's own browser is nobody's)
    webbrowser.open(url)
    return dict(_connect)


def disconnect() -> None:
    """Recap Studio's access taken back (TikTok's revoke), and the sign-in forgotten here either way."""
    token, c = _read("token.json", {}), _read("client.json", {})
    if token.get("refresh_token") and c.get("client_key"):
        try:
            requests.post(REVOKE_URL, data={"client_key": c["client_key"], "client_secret": c["client_secret"],
                                             "token": _access()}, timeout=15)
        except (PostError, requests.RequestException):
            pass
    (_plugin.data_dir / "token.json").unlink(missing_ok=True)


# ---- asking TikTok -------------------------------------------------------------------------------------------------

def _pause(job, seconds: float, why: str = "") -> None:
    """A wait a job can be paused or cancelled in (and says why); outside a job, a plain wait."""
    if job is None:
        time.sleep(seconds)
        return
    if why:
        job["message"] = f"Posting to TikTok: {why}, trying again in {max(1, round(seconds))} s"
    end = time.time() + seconds
    while time.time() < end:
        pipeline._check(job)
        time.sleep(min(0.2, max(0.0, end - time.time())))


def _retry_after(r: requests.Response, attempt: int) -> float:
    try:
        return min(300.0, max(1.0, float(r.headers.get("Retry-After", ""))))
    except ValueError:
        return min(300.0, BACKOFF * 2 ** attempt)


def _error(code: str, message: str) -> PostError:
    if code in LIMITS:
        return LimitError(LIMITS[code], code)
    if code in HINTS:  # (said in plain words, what to do included)
        return PostError(HINTS[code], code)
    return PostError(f"TikTok said: {message}" + (f" ({code})" if code else ""), code)


def _call(path: str, body: dict | None = None, job=None, method: str = "POST", params: dict | None = None) -> dict:
    """One of TikTok's API calls: its "data", or PostError with what it said. "Too many requests" (429) is waited
    out and tried again, a sign-in that ran out is renewed once, and TikTok's own trouble (5xx) is tried again."""
    fresh = renewed = False
    for attempt in range(TRIES):
        headers = {"Authorization": f"Bearer {_access(fresh)}"}
        fresh = False
        if method == "POST":
            headers["Content-Type"] = "application/json; charset=UTF-8"
        try:
            r = _session.request(method, API + path, params=params, headers=headers, timeout=60,
                                 data=json.dumps(body) if body is not None else None)
        except requests.RequestException as e:
            if attempt == TRIES - 1:
                raise PostError(f"TikTok didn't answer: {e}")
            _pause(job, BACKOFF * 2 ** attempt, "TikTok didn't answer")
            continue
        try:
            found = r.json()
        except ValueError:
            found = {}
        err = found.get("error") if isinstance(found, dict) and isinstance(found.get("error"), dict) else {}
        code = err.get("code") or ""
        if r.status_code == 429 or code == "rate_limit_exceeded":
            _pause(job, _retry_after(r, attempt), "TikTok asked for a pause (too many requests)")
            continue
        if r.status_code >= 500:
            _pause(job, BACKOFF * 2 ** attempt, "TikTok had trouble")
            continue
        if (r.status_code == 401 or code == "access_token_invalid") and not renewed:
            fresh = renewed = True
            continue
        if r.status_code >= 400 or (code and code != "ok"):
            raise _error(code, err.get("message") or r.reason or str(r.status_code))
        return found.get("data") or {}
    raise PostError("TikTok kept saying it had too many requests (or had trouble): try again in a few minutes")


def creator_info(job=None) -> dict:
    """What this account can post now (TikTok wants it asked each time a post is made ready): its name, who may see
    a post, whether comments, duets and stitches can be allowed, and the longest video it may post."""
    data = _call("/v2/post/publish/creator_info/query/", job=job)
    return {"nickname": data.get("creator_nickname") or "", "username": data.get("creator_username") or "",
            "privacy": [x for x in data.get("privacy_level_options") or [] if x in PRIVACY],
            "comments_off": bool(data.get("comment_disabled")), "duet_off": bool(data.get("duet_disabled")),
            "stitch_off": bool(data.get("stitch_disabled")),
            "longest": int(data.get("max_video_post_duration_sec") or 0)}


def _status_of(publish_id: str, job=None) -> dict:
    return _call("/v2/post/publish/status/fetch/", {"publish_id": publish_id}, job=job)


def _cancel(publish_id: str) -> None:
    """Best effort: a post whose upload didn't finish, called off (TikTok can't post half a video anyway)."""
    try:
        _session.post(f"{API}/v2/post/publish/cancel/", json={"publish_id": publish_id}, timeout=15,
                      headers={"Authorization": f"Bearer {_access()}"})
    except (PostError, requests.RequestException):
        pass


# ---- what a project posts --------------------------------------------------------------------------------------------

def _runes(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _cut(text: str, room: int) -> str:
    if _runes(text) <= room:
        return text
    out, used = [], 0
    for ch in text:
        w = 2 if ord(ch) > 0xFFFF else 1
        if used + w > room - 1:
            break
        out.append(ch)
        used += w
    return "".join(out).rstrip() + "…"


def default_tag(name: str) -> str:
    """The series' hashtag: its name in one word, then Recap ("Solo Leveling" → SoloLevelingRecap)."""
    words = re.findall(r"[^\W_]+", name)
    return "".join(w[:1].upper() + w[1:] for w in words)[:40] + "Recap"


def clean_tag(tag: str) -> str:
    return re.sub(r"[^\w]", "", (tag or "").strip().lstrip("#"))[:60]


def _hashtags(text: str) -> list[str]:
    return list(dict.fromkeys(f"#{t}" for t in (clean_tag(x) for x in re.split(r"[\s,]+", text or "")) if t))


def caption(title: str, n: int, total: int, tag: str, description: str = "", hashtags=()) -> str:
    """A post's caption: the title ("(Part 2 of 5)" in a series), the description, and the series' hashtag
    ("Every part: #…" in a series) with Settings' hashtags. Fitted to TikTok's 2200, the description cut first."""
    head = _cut(title + (f" (Part {n} of {total})" if total > 1 else ""), CAPTION // 2)
    tags = [f"#{tag}"] + [h for h in hashtags if h.lower() != f"#{tag}".lower()]
    foot = _cut(("Every part: " if total > 1 else "") + " ".join(tags), CAPTION // 4)
    room = CAPTION - _runes(head) - _runes(foot) - 4
    description = _cut(description.strip(), room).strip() if room > 1 else ""
    return "\n\n".join(x for x in (head, description, foot) if x)


def _youtube_choice(pid: str) -> dict:
    """Post to YouTube's choices for the project (its title and description), when it has some."""
    return (_read_json(_plugin.data_dir.parent / "youtube-post" / "projects.json") or {}).get(pid) or {}


def _title(p: dict, choice: dict) -> str:
    return re.sub(r"\s+", " ", choice.get("title") or _youtube_choice(p["id"]).get("title")
                  or (p.get("youtube") or {}).get("title") or p["name"]).strip()


def _description(p: dict, choice: dict, title: str, n: int) -> str:
    text = choice.get("description") or _youtube_choice(p["id"]).get("description") or ""
    for k, v in (("title", title), ("series", p["name"]), ("chapters", ""), ("part", str(n))):
        text = text.replace("{" + k + "}", v)  # (Post to YouTube's places; its chapter list means nothing on TikTok)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _pieces(p: dict) -> list[Path]:
    """Advanced Cutting's pieces of the project, in order: its last cut into the folder its Settings name now (its
    settings.json and made.json), else its last cut anywhere."""
    folder = _plugin.data_dir.parent / "advanced-cutting"
    made = (_read_json(folder / "made.json") or {}).get(p["id"]) or {}
    if not made:
        return []
    s = _read_json(folder / "settings.json") or {}
    if s.get("folder"):
        dest = Path(s["folder"]) / (store.folder_name(p["name"]) if s.get("own_folder", True) else "")
    else:
        dest = store.project_dir(p["id"]) / "output" / "Pieces"
    names = made.get(str(dest))
    if names is None:
        last = list(made)[-1]
        dest, names = Path(last), made[last]
    return [dest / x for x in names if x.endswith(".mp4") and (dest / x).is_file()]


def _files(p: dict, which: str) -> list[dict]:
    """A set of the project's videos, in order: "video" (the whole video, or its parts when the app split it),
    "shorts" (the output folder's shorts folder) or "pieces" (Advanced Cutting's)."""
    d = store.project_dir(p["id"])
    out = p.get("output") or {}
    if which == "video":
        parts = out.get("parts") or ([{"video": out["video"]}] if out.get("video") else [])
        return [{"path": d / x["video"], "seconds": float(x.get("seconds") or 0)} for x in parts if (d / x["video"]).is_file()]
    if which == "shorts":
        if out.get("shorts"):
            paths = [d / x for x in out["shorts"]]
        else:
            paths = [f for x in _files(p, "video") for f in
                     sorted((x["path"].parent / "shorts").glob(f"{glob_escape(x['path'].stem)} - Short *.mp4"))]
        return [{"path": f, "seconds": 0.0} for f in paths if f.is_file()]
    return [{"path": f, "seconds": 0.0} for f in _pieces(p)]


def _set_key(choice: dict, s: dict) -> str:
    k = choice.get("set") or s["set"]
    return k if k in SETS else "video"


def plan(p: dict, choice: dict, s: dict) -> dict:
    """What the project posts, in order, each with its caption and its record's key (its file's name and size)."""
    key = _set_key(choice, s)
    files = _files(p, key)
    title, tag = _title(p, choice), clean_tag(choice.get("tag") or "") or default_tag(p["name"])
    hashtags = _hashtags(s["hashtags"])
    items = []
    for n, f in enumerate(files, 1):
        size = f["path"].stat().st_size
        total = len(files)
        items.append({"path": f["path"], "file": f["path"].name, "size": size, "key": f"{f['path'].name}|{size}",
                      "seconds": f["seconds"], "n": n, "of": total,
                      "title": title + (f" (Part {n} of {total})" if total > 1 else ""),
                      "caption": caption(title, n, total, tag, _description(p, choice, title, n), hashtags)})
    return {"set": key, "items": items, "title": title, "tag": tag, "why": "" if items else WHY[key]}


def _state(recs: dict, it: dict) -> str:
    """new, sending (sent; TikTok was still processing it), posted, or changed (a file of that name was posted, at
    another size: the video was made again since)."""
    rec = recs.get(it["key"])
    if rec:
        return rec.get("state") or "posted"
    return "changed" if any(r.get("file") == it["file"] for r in recs.values()) else "new"


def _label(it: dict) -> str:
    return f"Part {it['n']} of {it['of']}" if it["of"] > 1 else "the video"


def _minutes(seconds: float) -> str:
    if seconds < 90:
        return f"{round(seconds)} seconds"
    return f"{round(seconds / 60)} minutes"


def _seconds(path: Path) -> float:
    """How long a video runs (ffmpeg's reading of it), or 0 when it can't be told."""
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime)
    if key not in _lengths:
        found = 0.0
        try:
            from recap import video
            ffmpeg = video.find_ffmpeg()
            if ffmpeg:
                r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                                   errors="replace", timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
                found = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else 0.0
        except Exception:
            pass
        _lengths[key] = found
    return _lengths[key]


def _privacy(choice: dict, s: dict, info: dict | None = None) -> str:
    """Who sees the posts: Only you, until the member's app has passed TikTok's audit (TikTok's rule); else what
    they chose (no default), and only one TikTok offers this account now."""
    if not s["audited"]:
        return "SELF_ONLY"
    want = choice.get("privacy") or s["privacy"]
    if want not in PRIVACY:
        raise PostError("Choose who sees it first (Post to TikTok… at Publish, or Settings → Post to TikTok)")
    if info is not None and info["privacy"] and want not in info["privacy"]:
        offered = ", ".join(PRIVACY[x] for x in info["privacy"])
        raise PostError(f"TikTok doesn't offer \"{PRIVACY[want]}\" to this account now (it offers {offered}): choose again",
                        "privacy_level_option_mismatch")
    return want


def status(pid: str) -> dict:
    """For the page: the sets the project has, what the chosen one posts, and where each stands."""
    p = store.load(pid)
    choice, s = _choice(pid), _settings()
    pl, recs = plan(p, choice, s), _posted(pid)
    d = store.project_dir(pid).resolve()
    items = []
    for it in pl["items"]:
        rec = recs.get(it["key"]) or {}
        items.append({"file": it["file"], "n": it["n"], "of": it["of"], "title": it["title"], "caption": it["caption"],
                      "state": _state(recs, it), "privacy": rec.get("privacy"), "public": bool(rec.get("public_id")),
                      "url": rec.get("url") or "", "at": rec.get("at")})
    first = pl["items"][0]["path"].resolve() if pl["items"] else None
    return {"choice": choice, "settings": s, "set": pl["set"], "why": pl["why"], "items": items, "title": pl["title"],
            "tag": pl["tag"], "default_tag": default_tag(p["name"]), "auto": p["settings"].get(_key, "no"),
            "sets": {k: len(_files(p, k)) for k in SETS}, "user": _user(), "blocked": _blocked(),
            "preview": first.relative_to(d).as_posix() if first and d in first.parents else None}


# ---- posting ---------------------------------------------------------------------------------------------------------

def _upload(job, url: str, path: Path, size: int, chunk: int, count: int, report) -> None:
    """The video, in pieces, one after another (TikTok's FILE_UPLOAD): 206 for each piece, 201 when it's whole. A
    piece that fails is sent again."""
    kind = {".mov": "video/quicktime", ".webm": "video/webm"}.get(path.suffix.lower(), "video/mp4")
    k = tries = 0
    with open(path, "rb") as f:
        while k < count:
            pipeline._check(job)
            a = k * chunk
            b = size - 1 if k == count - 1 else a + chunk - 1  # (the last piece takes what's left)
            f.seek(a)
            data = f.read(b - a + 1)
            try:
                r = _session.put(url, data=data, timeout=600, headers={
                    "Content-Type": kind, "Content-Length": str(len(data)), "Content-Range": f"bytes {a}-{b}/{size}"})
            except requests.RequestException:
                r = None
            if r is not None and r.status_code in (201, 206):
                k, tries = k + 1, 0
                report((b + 1) / size)
                if r.status_code == 201:
                    return
                continue
            if r is not None and r.status_code == 403:
                raise PostError("TikTok's upload address ran out (an upload has an hour): post it again")
            if r is not None and r.status_code in (400, 404):
                raise PostError(f"TikTok refused the upload ({r.status_code}): post it again")
            if r is not None and r.status_code == 416:  # (TikTok has another amount: carry on from the piece it's at)
                got = re.search(r"bytes \d+-(\d+)/", r.headers.get("Content-Range", ""))
                if not got:
                    raise PostError("TikTok lost track of the upload: post it again")
                k = min(count - 1, (int(got.group(1)) + 1) // chunk)
            tries += 1
            if tries >= TRIES:
                raise PostError("The upload kept failing: try again later")
            _pause(job, BACKOFF * 2 ** (tries - 1), "a piece of the video didn't go up")


def _wait_published(job, publish_id: str, label: str) -> dict | None:
    """TikTok's word on a post it was sent: PUBLISH_COMPLETE or FAILED, or None while it's still processing."""
    end = time.time() + PROCESSING
    while True:
        st = _status_of(publish_id, job)
        if st.get("status") in ("PUBLISH_COMPLETE", "FAILED"):
            return st
        if time.time() > end:
            return None
        job["message"] = f"Posting to TikTok: TikTok is processing {label}"
        _pause(job, POLL)


def _public_ids(st: dict) -> list:
    return st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []  # (TikTok's spelling first)


def _done(pid: str, key: str, rec: dict, st: dict) -> dict:
    """A post TikTok says is up: kept as posted, with its public id and address once TikTok gives them."""
    rec.update(state="posted", at=_clock(time.time()), looked=time.time())
    ids = _public_ids(st)
    if ids:
        username = (_user() or {}).get("username") or ""
        rec["public_id"] = str(ids[0])
        rec["url"] = f"https://www.tiktok.com/@{username}/video/{ids[0]}" if username else ""
    _record(pid, key, rec)
    store.update(pid, lambda p, at=rec["at"]: p.setdefault("posted", {}).update({"TikTok": at}))  # (the library's line)
    return rec


def _settle(pid: str, job=None) -> int:
    """Posts sent before that weren't settled (the app closed, or the job stopped while TikTok processed them):
    looked up, so nothing is sent twice. Gives how many TikTok is still working on."""
    still = 0
    for key, rec in list(_posted(pid).items()):
        if rec.get("state") != "sending":
            continue
        try:
            st = _status_of(rec["publish_id"], job)
        except LimitError:
            raise
        except PostError as e:
            if e.code in ("invalid_publish_id", "token_not_authorized_for_specified_publish_id"):
                _forget(pid, key)
            else:
                still += 1
            continue
        if st.get("status") == "PUBLISH_COMPLETE":
            _done(pid, key, rec, st)
        elif st.get("status") == "FAILED" or (not rec.get("uploaded") and time.time() - rec.get("since", 0) > 3900):
            _forget(pid, key)  # (not posted after all: posted again next time)
        else:
            still += 1
    return still


def _send(job, p: dict, pl: dict, it: dict, privacy: str, flags: dict, report) -> dict | None:
    """One video: TikTok told what's coming (its caption, who sees it), the file sent in pieces, then TikTok's
    word on it. Gives its record, or None while TikTok is still processing it (looked up next time)."""
    pid, size = p["id"], it["size"]
    chunk = size if size < CHUNK else CHUNK
    count = max(1, size // chunk)
    data = _call("/v2/post/publish/video/init/", {
        "post_info": {"title": it["caption"], "privacy_level": privacy, **flags},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}},
        job=job)
    if not data.get("publish_id") or not data.get("upload_url"):
        raise PostError("TikTok gave nowhere to send the video")
    rec = {"file": it["file"], "size": size, "set": pl["set"], "n": it["n"], "of": it["of"], "title": it["title"],
           "tag": pl["tag"], "publish_id": data["publish_id"], "privacy": privacy, "state": "sending",
           "uploaded": False, "since": time.time(), "at": _clock(time.time())}
    _record(pid, it["key"], rec)  # (before anything is sent: a post is never sent twice)
    try:
        _upload(job, data["upload_url"], it["path"], size, chunk, count, report)
    except BaseException:
        _forget(pid, it["key"])  # (TikTok can't post half a video: it isn't counted as sent)
        _cancel(data["publish_id"])
        raise
    rec["uploaded"] = True
    _record(pid, it["key"], rec)
    st = _wait_published(job, data["publish_id"], _label(it))
    if st is None:
        return None
    if st["status"] == "FAILED":
        _forget(pid, it["key"])
        reason = st.get("fail_reason") or ""
        if reason in LIMITS:
            raise LimitError(LIMITS[reason], reason)
        raise PostError(f"TikTok didn't post {_label(it)}: {FAILS.get(reason, reason or 'it gave no reason')}", reason)
    return _done(pid, it["key"], rec, st)


def _seen_as(rec: dict) -> str:
    """Who sees a post, as far as TikTok has said: public only once TikTok gives its public id."""
    if rec.get("privacy") == "PUBLIC_TO_EVERYONE":
        return "public" if rec.get("public_id") else "not public yet"
    return {"SELF_ONLY": "only you can see it", "MUTUAL_FOLLOW_FRIENDS": "for your friends",
            "FOLLOWER_OF_CREATOR": "for your followers"}.get(rec.get("privacy"), "")


def _post(job, p: dict, choice: dict, s: dict, again: bool, sent: list) -> str:
    pid = p["id"]
    pl = plan(p, choice, s)
    if not pl["items"]:
        return pl["why"]
    job["message"] = "Posting to TikTok: looking up what was sent before"
    still = _settle(pid, job)
    recs = _posted(pid)
    todo = [it for it in pl["items"] if _state(recs, it) == "new" or (again and _state(recs, it) == "changed")]
    if not todo:
        return "Nothing new to post" + (f" (TikTok is still processing {still})" if still else "")
    job["message"] = "Posting to TikTok: asking TikTok what this account can post"
    info = creator_info(job)
    _remember_user(info)
    privacy = _privacy(choice, s, info)
    if s["disclosure"] in ("branded", "both") and privacy == "SELF_ONLY":
        raise PostError("TikTok doesn't let branded content be seen by only you: choose who sees it, or change the "
                        "content disclosure in Settings → Post to TikTok")
    longest, problems = info["longest"], []
    for it in todo:  # (all checked first: a series with a part missing wouldn't make sense)
        it["seconds"] = it["seconds"] or _seconds(it["path"])
        if it["size"] > LARGEST:
            problems.append(f"{_label(it)} is over 4 GB")
        elif longest and it["seconds"] > longest + 1:
            problems.append(f"{_label(it)} runs {_minutes(it['seconds'])}")
    if problems:
        raise PostError(f"TikTok lets this account post videos of up to {_minutes(longest)}, and {'; '.join(problems)}. "
                        "Post its Shorts instead, or Advanced Cutting's pieces cut by length (0.15 hours is 9 minutes). "
                        "Nothing was posted")
    flags = {"disable_comment": not s["comments"] or info["comments_off"],
             "disable_duet": not s["duet"] or info["duet_off"], "disable_stitch": not s["stitch"] or info["stitch_off"],
             "brand_organic_toggle": s["disclosure"] in ("yours", "both"),
             "brand_content_toggle": s["disclosure"] in ("branded", "both"), "is_aigc": bool(s["aigc"])}
    total, base = sum(it["size"] for it in todo), 0
    for k, it in enumerate(todo):
        if k:
            job["message"] = f"Posting to TikTok: a pause before {_label(it)}, so TikTok isn't asked too fast"
            _pause(job, GAP)

        def report(f, it=it, base=base):
            job["progress"] = min(0.99, (base + f * it["size"]) / max(1, total))
            job["message"] = f"Posting to TikTok: {_label(it)} ({round(f * 100)}%)"
        report(0)
        rec = _send(job, p, pl, it, privacy, flags, report)
        base += it["size"]
        if rec is None:
            still += 1
            _log(p["name"], f"{it['title']}: sent; TikTok is still processing it")
        else:
            sent.append(rec)
            _log(p["name"], f"{it['title']}: posted ({_seen_as(rec)})")
    note = f"Posted {len(sent)} to TikTok" if sent else "Nothing posted"
    seen = sorted({_seen_as(r) for r in sent} - {""})
    if seen:
        note += f" ({', '.join(seen)})"
    if still:
        note += f"; TikTok is still processing {still}, it's looked up next time"
    return note


def post(job) -> None:
    """The job step: post what the project's chosen set hasn't posted yet, in order."""
    pid = job["project"]
    p, choice, s = store.load(pid), _choice(pid), _settings()
    if not _connected():
        _set_pending(pid, False)
        raise PostError("No TikTok account is connected (Settings → Post to TikTok)")
    blocked = _blocked()
    if blocked:
        _set_pending(pid, True)
        job["note"] = f"{blocked['why']}: the rest go up after {blocked['until']}"
        return
    sent: list = []
    try:
        job["note"] = _post(job, p, choice, s, bool((job.get("options") or {}).get("again")), sent)
    except LimitError as e:
        _block(str(e))
        _set_pending(pid, True)
        note = (f"Posted {len(sent)} to TikTok; " if sent else "") + f"{e}: the rest go up after {_blocked()['until']}"
        _log(p["name"], note)
        job["note"] = note
        return
    except pipeline.Cancelled:
        _set_pending(pid, False)  # (stopped by you: not carried on by itself)
        raise
    except Exception as e:  # (needs you: not tried again by itself)
        _set_pending(pid, False)
        _log(p["name"], f"Not posted: {e}")
        raise
    _set_pending(pid, False)


# ---- when it posts by itself ---------------------------------------------------------------------------------------

def _after_job(job: dict) -> None:
    """When a project's video is made: post it, if its Project settings say so."""
    if job["status"] != "done" or "video" not in (job.get("steps") or []):
        return
    pid = job["project"]
    try:
        wanted = store.load(pid)["settings"].get(_key, "no") == "yes"
    except Exception:
        return
    if not wanted or not _connected():
        return
    _set_pending(pid, True, since=time.time())
    if _set_key(_choice(pid), _settings()) == "pieces":
        return  # (Advanced Cutting cuts it after this: _look posts the pieces once they're there)
    try:
        _plugin.run(pid, "post")
    except Exception as e:  # (a job started meanwhile, Post to YouTube's say: _look tries again)
        print("Post to TikTok:", e)


def _pieces_ready(p: dict, since: float) -> bool:
    """Advanced Cutting has cut the video made then: nothing of the project waiting to be cut, and pieces newer
    than the video."""
    cutting = sys.modules.get("recap_plugin_advanced_cutting")
    if cutting and (p["id"] in getattr(cutting, "_waiting", []) or getattr(cutting, "_now", {}).get("project") == p["id"]):
        return False
    pieces, videos = _pieces(p), _files(p, "video")
    if not pieces or not videos:
        return False
    return min(f.stat().st_mtime for f in pieces) >= max(v["path"].stat().st_mtime for v in videos) - 1


def _check_public() -> None:
    """Posts asked to be public that TikTok hasn't given a public id yet (it does once its checks pass, within
    minutes or hours): looked up again, a few a minute, for two days."""
    looked, now = 0, time.time()
    for pid, recs in _read("posted.json", {}).items():
        for key, rec in recs.items():
            if (rec.get("state") != "posted" or rec.get("public_id") or rec.get("privacy") != "PUBLIC_TO_EVERYONE"
                    or now - rec.get("since", 0) > 2 * 86400 or now - rec.get("looked", 0) < LOOK_AGAIN):
                continue
            if looked >= 5:
                return
            looked += 1
            try:
                st = _status_of(rec["publish_id"])
            except PostError:
                continue
            _done(pid, key, rec, st)


def _look() -> None:
    """Every minute: public ids TikTok hasn't given yet, and the projects with posting still to do (a video just
    made whose post couldn't start, Advanced Cutting's pieces once they're cut, the rest after a daily limit)."""
    if not _connected():
        return
    _check_public()
    if _blocked():
        return
    for pid, info in sorted(_read("pending.json", {}).items()):
        try:
            p = store.load(pid)
        except Exception:
            _set_pending(pid, False)
            continue
        job = _plugin.job(pid)
        if job and job["status"] in pipeline.ACTIVE:
            continue
        since = (info or {}).get("since", 0)
        if _set_key(_choice(pid), _settings()) == "pieces" and not _pieces_ready(p, since):
            if time.time() - since > WAIT_PIECES:
                _set_pending(pid, False)
                _log(p["name"], "Not posted: Advanced Cutting's pieces of the new video never came (Cut the video… "
                                "at the Video stage, then Post to TikTok…)")
            continue
        try:
            _plugin.run(pid, "post")
        except Exception as e:
            print("Post to TikTok:", e)


# ---- the plugin's API --------------------------------------------------------------------------------------------------

def _account() -> dict:
    c = _read("client.json", {})
    key = c.get("client_key", "")
    return {"client": bool(key and c.get("client_secret")), "client_key": key[:6] + ("…" if len(key) > 6 else ""),
            "user": _user(), "connect": {k: _connect[k] for k in ("state", "error")}, "server": on_server(),
            "settings": _settings(), "blocked": _blocked(), "redirect": REDIRECT, "privacy": PRIVACY,
            "music": MUSIC, "branded": BRANDED,
            "log": [{**x, "at": _clock(x["at"])} for x in _read("log.json", [])]}


def setup(plugin):
    global _plugin, _key
    _plugin = plugin
    _key = plugin.project_setting("auto", "no")
    plugin.step("post", "Posting to TikTok", post)
    plugin.on("job_done", _after_job)
    plugin.every(60, _look)

    def safely(fn, *args):
        try:
            return fn(*args)
        except PostError as e:
            raise HTTPException(400, str(e))
        except requests.RequestException as e:
            raise HTTPException(502, f"TikTok didn't answer: {e}")

    @plugin.router.get("/account")
    def account():
        return _account()

    @plugin.router.put("/client")
    def set_client(client_key: str = Body(..., embed=True), client_secret: str = Body(..., embed=True)):
        client_key, client_secret = client_key.strip(), client_secret.strip()
        if not re.fullmatch(r"[A-Za-z0-9]{8,64}", client_key) or not re.fullmatch(r"[A-Za-z0-9_\-]{8,128}", client_secret):
            raise HTTPException(400, "That isn't a TikTok app's client key and secret (your app's page on TikTok for "
                                     "Developers shows them)")
        _write("client.json", {"client_key": client_key, "client_secret": client_secret})
        return _account()

    @plugin.router.put("/settings")
    def set_settings(settings: dict = Body(..., embed=True)):
        s = _settings()
        for k, v in settings.items():
            if k not in DEFAULT_SETTINGS:
                continue
            if isinstance(DEFAULT_SETTINGS[k], bool):
                s[k] = bool(v)
            elif k == "privacy" and v not in ("", *PRIVACY):
                raise HTTPException(400, f"Who sees it can't be {v}")
            elif k == "set" and v not in SETS:
                raise HTTPException(400, f"What it posts can't be {v}")
            elif k == "disclosure" and v not in DISCLOSURE:
                raise HTTPException(400, f"The content disclosure can't be {v}")
            else:
                s[k] = " ".join(_hashtags(str(v))) if k == "hashtags" else str(v)
        _write("settings.json", s)
        return _account()

    @plugin.router.post("/connect")
    def connect():
        return safely(start_connect)

    @plugin.router.post("/connect/paste")
    def connect_paste(address: str = Body(..., embed=True)):
        return safely(lambda: paste_answer(address))

    @plugin.router.post("/disconnect")
    def drop():
        disconnect()
        return _account()

    @plugin.router.get("/creator")
    def creator():
        """What the account can post now, asked fresh each time Post to TikTok… opens (as TikTok wants)."""
        info = safely(creator_info)
        _remember_user(info)
        return {**info, "audited": _settings()["audited"],
                "public_account": "PUBLIC_TO_EVERYONE" in info["privacy"]}

    @plugin.router.get("/projects/{pid}")
    def project_status(pid: str):
        try:
            return status(pid)
        except KeyError:
            raise HTTPException(404, "No such project")

    @plugin.router.put("/projects/{pid}")
    def set_choice(pid: str, choice: dict = Body({}, embed=True), auto: str | None = Body(None, embed=True)):
        try:
            store.load(pid)
        except KeyError:
            raise HTTPException(404, "No such project")
        clean = {k: str(choice[k]).strip() for k in DEFAULT_CHOICE if k in choice and choice[k] is not None}
        if clean.get("set") not in (None, "", *SETS):
            raise HTTPException(400, f"What it posts can't be {clean['set']}")
        if clean.get("privacy") not in (None, "", *PRIVACY):
            raise HTTPException(400, f"Who sees it can't be {clean['privacy']}")
        for k, longest in (("title", 300), ("description", 2000)):
            if k in clean:
                clean[k] = clean[k][:longest]
        if "tag" in clean:
            clean["tag"] = clean_tag(clean["tag"])
        with _lock:
            choices = _read("projects.json", {})
            choices[pid] = {**DEFAULT_CHOICE, **choices.get(pid, {}), **clean}
            _write("projects.json", choices)
        if auto is not None:
            if auto not in ("yes", "no"):
                raise HTTPException(400, f"Posting by itself can't be {auto}")
            store.update(pid, lambda p: p["settings"].update({_key: auto}))
        return status(pid)

    @plugin.router.post("/projects/{pid}/post")
    def post_now(pid: str, again: bool = Body(False, embed=True)):
        try:
            store.load(pid)
        except KeyError:
            raise HTTPException(404, "No such project")
        if not _connected():
            raise HTTPException(400, "Connect your TikTok account first (Settings → Post to TikTok)")
        job = pipeline.latest_for(pid)
        if job and job["status"] in pipeline.ACTIVE:
            raise HTTPException(409, "A job is already running for this project")
        return pipeline.public(pipeline.submit(pid, [f"{plugin.id}.post"], options={"again": again}))
