"""A stand-in for TikTok's sign-in and its Content Posting API (direct post), for testing Post to TikTok on this PC.
It follows TikTok's documentation as of November 2026:

    token     POST /v2/oauth/token/ (form: authorization_code with PKCE, its challenge the verifier's SHA-256 in hex;
              refresh_token, which hands out a new refresh token and makes the old one useless), POST /v2/oauth/revoke/
    user      GET /v2/user/info/?fields=open_id,display_name,avatar_url
    creator   POST /v2/post/publish/creator_info/query/ (privacy options by whether the account is private)
    init      POST /v2/post/publish/video/init/ (FILE_UPLOAD: video_size, chunk_size, total_chunk_count checked
              against TikTok's rules) -> {"data": {"publish_id", "upload_url"}}
    upload    PUT /upload/<n> in order, with Content-Range: 206 for a piece, 201 when the video is whole
    status    POST /v2/post/publish/status/fetch/ -> PROCESSING_UPLOAD, then PUBLISH_COMPLETE or FAILED, with
              publicaly_available_post_id (TikTok's spelling) only for a public post of an audited app
    cancel    POST /v2/post/publish/cancel/
Every answer has {"error": {"code": "ok" or why, "message", "log_id"}}. What it does wrong on purpose (or how the
account and the app are) is set in CONFIG by the test."""
import hashlib
import json
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MB = 1024 * 1024
CONFIG = {"private": True,          # the account is private (TikTok's privacy options follow it)
          "audited": False,         # the member's app has passed TikTok's audit
          "longest": 600,           # max_video_post_duration_sec
          "comment_disabled": False, "duet_disabled": True, "stitch_disabled": False,
          "limit_inits": 0,         # the next so many inits answered 429 rate_limit_exceeded
          "fail_piece_once": None,  # the piece (counted from 1 over every upload) answered 503 once
          "daily_cap": None,        # posts after which init says spam_risk_too_many_posts
          "polls": 1,               # status looks answered PROCESSING_UPLOAD after a video is whole
          "public_ids": False,      # a public post of an audited app gets its public id (its checks passed)
          "fail_reason": "",        # the next post FAILED with this fail_reason
          "scope": "user.info.basic,video.publish"}
SEEN = {"challenge": None, "redirect": None, "verifier_ok": None, "inits": [], "limited": 0, "revoked": [],
        "refreshes": 0, "pieces": 0, "failed_once": False, "cancelled": [], "bad": [], "init_at": []}
TOKENS = {}                 # access token -> scope
REFRESH = {"current": None}  # the one refresh token that works
_posts: dict[str, dict] = {}  # publish_id -> {"init", "size", "chunk", "count", "data", "polls", "state"}
_lock = threading.Lock()
_n = {"code": 0, "post": 0, "public": 7400000000000000000}


def expire_access() -> None:
    """Every access token handed out stops working (as two hours later)."""
    TOKENS.clear()


def make_public() -> None:
    """TikTok's checks pass for the public posts it was asked for: they get their public ids."""
    with _lock:
        for p in _posts.values():
            if p["state"] == "PUBLISH_COMPLETE" and p["init"]["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE" \
                    and CONFIG["audited"] and not p.get("public"):
                _n["public"] += 1
                p["public"] = _n["public"]


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, data=None, error="ok", message="", headers=None, raw=None):
        body = raw if raw is not None else {"data": data or {}, "error": {"code": error, "message": message, "log_id": "test"}}
        out = json.dumps(body).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _authorized(self):
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        if token not in TOKENS:
            self._reply(401, error="access_token_invalid", message="The access token is invalid or not found in the request.")
            return None
        if "video.publish" not in TOKENS[token].split(","):
            self._reply(401, error="scope_not_authorized", message="The user did not authorize the scope required.")
            return None
        return token

    def log_message(self, *args):
        pass

    def _token(self):
        form = urllib.parse.parse_qs(self._body().decode())
        g = lambda k: (form.get(k) or [""])[0]  # noqa: E731
        if g("client_key") != "awtestclientkey1" or g("client_secret") != "testsecret0123456789":
            return self._reply(200, raw={"error": "invalid_client", "error_description": "Client key or secret is incorrect.", "log_id": "t"})
        with _lock:
            if g("grant_type") == "authorization_code":
                SEEN["verifier_ok"] = hashlib.sha256(g("code_verifier").encode()).hexdigest() == SEEN["challenge"]
                if g("code") != "good-code" or not SEEN["verifier_ok"] or (SEEN["redirect"] and g("redirect_uri") != SEEN["redirect"]):
                    return self._reply(200, raw={"error": "invalid_grant", "error_description": "Authorization code is expired.", "log_id": "t"})
                _n["code"] += 1
                access, refresh = f"act.{_n['code']}", f"rft.{_n['code']}"
            elif g("grant_type") == "refresh_token" and g("refresh_token") == REFRESH["current"]:
                SEEN["refreshes"] += 1
                access, refresh = f"act.r{SEEN['refreshes']}", f"rft.r{SEEN['refreshes']}"  # (a new one each time)
            else:
                return self._reply(200, raw={"error": "invalid_grant", "error_description": "Refresh token is invalid or expired.", "log_id": "t"})
            REFRESH["current"] = refresh
            TOKENS[access] = CONFIG["scope"]
        return self._reply(200, raw={"access_token": access, "expires_in": 7200, "open_id": "open-1",
                                     "refresh_expires_in": 15552000, "refresh_token": refresh, "scope": CONFIG["scope"],
                                     "token_type": "Bearer"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/v2/oauth/token/":
            return self._token()
        if path == "/v2/oauth/revoke/":
            form = urllib.parse.parse_qs(self._body().decode())
            SEEN["revoked"].append((form.get("token") or [""])[0])
            TOKENS.pop(SEEN["revoked"][-1], None)
            REFRESH["current"] = None
            return self._reply(200, raw={})
        body = json.loads(self._body() or b"{}")
        if not self._authorized():
            return
        if path == "/v2/post/publish/creator_info/query/":
            return self._reply(200, {
                "creator_avatar_url": "https://example.invalid/a.jpg", "creator_username": "testcreator",
                "creator_nickname": "Test Creator",
                "privacy_level_options": ["FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"] if CONFIG["private"]
                else ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
                "comment_disabled": CONFIG["comment_disabled"], "duet_disabled": CONFIG["duet_disabled"],
                "stitch_disabled": CONFIG["stitch_disabled"], "max_video_post_duration_sec": CONFIG["longest"]})
        if path == "/v2/post/publish/video/init/":
            return self._init(body)
        if path == "/v2/post/publish/status/fetch/":
            return self._status(body)
        if path == "/v2/post/publish/cancel/":
            SEEN["cancelled"].append(body.get("publish_id"))
            return self._reply(200)
        self._reply(404, error="not_found", message="no such thing")

    def _init(self, body):
        with _lock:
            SEEN["inits"].append(body)
            if CONFIG["limit_inits"] > 0:
                CONFIG["limit_inits"] -= 1
                SEEN["limited"] += 1
                return self._reply(429, error="rate_limit_exceeded", message="Rate limit exceeded")
            info, src = body.get("post_info") or {}, body.get("source_info") or {}
            options = ["FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"] if CONFIG["private"] \
                else ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]
            if not CONFIG["audited"] and not CONFIG["private"]:
                return self._reply(403, error="unaudited_client_can_only_post_to_private_accounts",
                                   message="Unaudited clients can only post to a private account")
            if info.get("privacy_level") not in options:
                return self._reply(400, error="privacy_level_option_mismatch", message="privacy_level is not among the options")
            if len((info.get("title") or "").encode("utf-16-le")) // 2 > 2200:
                return self._reply(400, error="invalid_param", message="title too long")
            size, chunk, count = src.get("video_size"), src.get("chunk_size"), src.get("total_chunk_count")
            ok = (src.get("source") == "FILE_UPLOAD" and isinstance(size, int) and isinstance(chunk, int) and size > 0
                  and count == size // chunk and 1 <= count <= 1000 and chunk <= 64 * MB
                  and (chunk >= 5 * MB or chunk == size) and size - (count - 1) * chunk <= 128 * MB)
            if not ok:
                SEEN["bad"].append(src)
                return self._reply(400, error="invalid_param", message=f"The chunk sizes are wrong: {src}")
            done = sum(1 for p in _posts.values() if p["state"] != "FAILED")
            if CONFIG["daily_cap"] is not None and done >= CONFIG["daily_cap"]:
                return self._reply(403, error="spam_risk_too_many_posts", message="The daily post cap from the API is reached")
            _n["post"] += 1
            SEEN["init_at"].append(time.time())
            pid = f"v_pub_file~v2-1.{_n['post']}"
            _posts[pid] = {"init": body, "size": size, "chunk": chunk, "count": count, "data": bytearray(), "polls": 0,
                           "state": "PROCESSING_UPLOAD", "n": _n["post"]}
        url = f"http://127.0.0.1:{self.server.server_address[1]}/upload/{_n['post']}?upload_id=x&upload_token=y"
        self._reply(200, {"publish_id": pid, "upload_url": url})

    def _status(self, body):
        with _lock:
            p = _posts.get(body.get("publish_id"))
            if not p:
                return self._reply(400, error="invalid_publish_id", message="publish_id does not exist")
            if p["state"] == "PROCESSING_UPLOAD" and len(p["data"]) == p["size"]:
                p["polls"] += 1
                if p["polls"] > CONFIG["polls"]:
                    p["state"] = "FAILED" if CONFIG["fail_reason"] else "PUBLISH_COMPLETE"
                    p["fail_reason"] = CONFIG["fail_reason"]
                    CONFIG["fail_reason"] = ""
            return self._reply(200, {"status": p["state"], "fail_reason": p.get("fail_reason", ""),
                                     "publicaly_available_post_id": [p["public"]] if p.get("public") else [],
                                     "uploaded_bytes": len(p["data"])})

    def do_PUT(self):
        url = urllib.parse.urlparse(self.path)
        m = re.fullmatch(r"/upload/(\d+)", url.path)
        data = self._body()
        if not m:
            return self._reply(404, error="not_found")
        with _lock:
            p = next((x for x in _posts.values() if x["n"] == int(m.group(1))), None)
            if not p:
                return self._reply(404, error="not_found")
            if self.headers.get("Content-Type") not in ("video/mp4", "video/quicktime", "video/webm"):
                return self._reply(400, error="invalid_param", message="Content-Type")
            got = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", self.headers.get("Content-Range", ""))
            if not got or int(self.headers.get("Content-Length") or -1) != len(data):
                return self._reply(400, error="invalid_param", message="Content-Range or Content-Length")
            a, b, total = (int(x) for x in got.groups())
            k = len(p["data"]) // p["chunk"]
            last = k == p["count"] - 1
            if total != p["size"] or a != len(p["data"]) or b - a + 1 != len(data) \
                    or (not last and len(data) != p["chunk"]) or (last and b != total - 1):
                SEEN["bad"].append((a, b, total, len(data)))
                return self._reply(416, error="invalid_param", headers={"Content-Range": f"bytes 0-{len(p['data']) - 1}/{p['size']}"})
            SEEN["pieces"] += 1
            if CONFIG["fail_piece_once"] == SEEN["pieces"] and not SEEN["failed_once"]:
                SEEN["failed_once"] = True
                return self._reply(503, error="internal_error", message="Gateway error")
            p["data"] += data
            whole = len(p["data"]) == p["size"]
        self._reply(201 if whole else 206, {}, headers={"Content-Range": f"bytes 0-{b}/{total}"})

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/v2/user/info/":
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
            if token not in TOKENS:
                return self._reply(401, error="access_token_invalid")
            return self._reply(200, {"user": {"open_id": "open-1", "display_name": "Test Creator",
                                              "avatar_url": "https://example.invalid/a.jpg"}})
        self._reply(404, error="not_found")


def posted() -> list[dict]:
    """What TikTok has: each post's caption, privacy, flags, its file's checksum and size, and its state."""
    with _lock:
        return [{"publish_id": k, "title": p["init"]["post_info"].get("title", ""), "post_info": p["init"]["post_info"],
                 "sha": hashlib.sha256(bytes(p["data"])).hexdigest(), "size": p["size"], "chunk": p["chunk"],
                 "count": p["count"], "state": p["state"], "public": p.get("public")}
                for k, p in sorted(_posts.items(), key=lambda kv: kv[1]["n"])]


def start(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
