"""A stand-in for Google's sign-in and YouTube's upload API, for testing Post to YouTube on this PC.

    token     POST /token (authorization_code with PKCE, refresh_token), POST /revoke
    channels  GET /youtube/v3/channels?part=snippet&mine=true
    uploads   POST /upload/youtube/v3/videos?uploadType=resumable  -> Location: /session/<n>
              PUT /session/<n>  pieces with Content-Range (308 + Range until whole, then 201 {"id"})
    thumbs    POST /upload/youtube/v3/thumbnails/set?videoId=
What it does wrong on purpose is set in CONFIG by the test."""
import base64
import hashlib
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = {"fail_piece_once": None, "expire_after_pieces": None, "quota": False, "thumb_forbidden": False}
SEEN = {"challenge": None, "verifier_ok": None, "uploads": [], "thumbs": [], "revoked": [], "refreshes": 0,
        "pieces": 0, "failed_once": False}
TOKENS = {"at-1"}
SCOPES = {}            # access token -> the scopes it was granted
SEEN.update(scope="", captions=[], playlist_items=[])
PLAYLISTS = {"PL-old": "Old stuff"}
FORCE = "https://www.googleapis.com/auth/youtube.force-ssl"
_sessions: dict[str, dict] = {}
_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _authorized(self):
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        if token not in TOKENS:
            self._reply(401, {"error": {"code": 401, "message": "Invalid Credentials", "errors": [{"reason": "authError"}]}})
            return False
        return True

    def _allowed(self):
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        if FORCE not in SCOPES.get(token, "").split():
            self._reply(403, {"error": {"code": 403, "message": "Request had insufficient authentication scopes.",
                                        "errors": [{"reason": "insufficientPermissions"}]}})
            return False
        return True

    def log_message(self, *args):
        pass

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path == "/token":
            form = urllib.parse.parse_qs(self._body().decode())
            g = lambda k: (form.get(k) or [""])[0]  # noqa: E731
            if g("grant_type") == "authorization_code":
                expect = SEEN["challenge"]
                got = base64.urlsafe_b64encode(hashlib.sha256(g("code_verifier").encode()).digest()).rstrip(b"=").decode()
                SEEN["verifier_ok"] = got == expect
                if g("code") != "good-code" or not SEEN["verifier_ok"]:
                    return self._reply(400, {"error": "invalid_grant", "error_description": "Bad code"})
                with _lock:
                    SEEN["codes"] = SEEN.get("codes", 0) + 1
                    token = "at-1" if SEEN["codes"] == 1 else f"at-c{SEEN['codes']}"
                    TOKENS.add(token)
                    SCOPES[token] = SCOPES["rt-1"] = SEEN["scope"]
                return self._reply(200, {"access_token": token, "expires_in": 3600, "refresh_token": "rt-1", "scope": SEEN["scope"]})
            if g("grant_type") == "refresh_token" and g("refresh_token") == "rt-1":
                with _lock:
                    SEEN["refreshes"] += 1
                    token = f"at-{SEEN['refreshes'] + 1}"
                    TOKENS.add(token)
                    SCOPES[token] = SCOPES.get("rt-1", "")
                return self._reply(200, {"access_token": token, "expires_in": 3600, "scope": SCOPES[token]})
            return self._reply(400, {"error": "invalid_grant"})
        if url.path == "/revoke":
            SEEN["revoked"].append((q.get("token") or [""])[0])
            return self._reply(200, {})
        if url.path == "/upload/youtube/v3/videos":
            meta = json.loads(self._body() or b"{}")
            if not self._authorized():
                return
            if CONFIG["quota"]:
                return self._reply(403, {"error": {"code": 403, "message": "The request cannot be completed because you have exceeded your quota.",
                                                   "errors": [{"reason": "quotaExceeded"}]}})
            with _lock:
                n = str(len(_sessions) + 1)
                _sessions[n] = {"meta": meta, "size": int(self.headers["X-Upload-Content-Length"]), "data": bytearray()}
            return self._reply(200, {}, {"Location": f"http://127.0.0.1:{self.server.server_address[1]}/session/{n}"})
        if url.path == "/upload/youtube/v3/captions":
            data = self._body()
            if not self._authorized() or not self._allowed():
                return
            if CONFIG["quota"]:
                return self._reply(403, {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
            boundary = self.headers["Content-Type"].split("boundary=")[1].encode()
            parts = [x for x in data.split(b"--" + boundary) if x.strip() not in (b"", b"--")]
            meta = json.loads(parts[0].split(b"\r\n\r\n", 1)[1].strip())
            srt = parts[1].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n")
            SEEN["captions"].append({"meta": meta, "srt": srt.decode("utf-8")})
            return self._reply(200, {"id": f"cap{len(SEEN['captions'])}", "snippet": meta["snippet"]})
        if url.path == "/youtube/v3/playlists":
            body = json.loads(self._body() or b"{}")
            if not self._authorized() or not self._allowed():
                return
            pid = f"PL-{len(PLAYLISTS) + 1}"
            PLAYLISTS[pid] = body["snippet"]["title"]
            SEEN.setdefault("playlists_made", []).append({"id": pid, **body})
            return self._reply(200, {"id": pid, "snippet": body["snippet"]})
        if url.path == "/youtube/v3/playlistItems":
            body = json.loads(self._body() or b"{}")
            if not self._authorized() or not self._allowed():
                return
            s = body["snippet"]
            if s["playlistId"] not in PLAYLISTS:
                return self._reply(404, {"error": {"code": 404, "message": "Playlist not found", "errors": [{"reason": "playlistNotFound"}]}})
            SEEN["playlist_items"].append((s["playlistId"], s["resourceId"]["videoId"]))
            return self._reply(200, {"id": f"PLI{len(SEEN['playlist_items'])}"})
        if url.path == "/upload/youtube/v3/thumbnails/set":
            data = self._body()
            if not self._authorized():
                return
            if CONFIG["thumb_forbidden"]:
                return self._reply(403, {"error": {"code": 403, "message": "The authenticated user doesn't have permissions to upload and set custom video thumbnails.",
                                                   "errors": [{"reason": "forbidden"}]}})
            SEEN["thumbs"].append({"video": (q.get("videoId") or [""])[0], "bytes": len(data)})
            return self._reply(200, {"items": [{"default": {"url": "x"}}]})
        self._reply(404, {"error": {"message": "no such thing"}})

    def do_PUT(self):
        url = urllib.parse.urlparse(self.path)
        m = re.fullmatch(r"/session/(\d+)", url.path)
        data = self._body()
        if not m:
            return self._reply(404, {})
        if not self._authorized():
            return
        s = _sessions[m.group(1)]
        rng = self.headers.get("Content-Range", "")
        if rng.startswith("bytes */"):  # where did it get to?
            return self._done(s) if len(s["data"]) == s["size"] else self._reply(
                308, None, {"Range": f"bytes=0-{len(s['data']) - 1}"} if s["data"] else {})
        a, b, total = (int(x) for x in re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", rng).groups())
        with _lock:
            SEEN["pieces"] += 1
            piece = SEEN["pieces"]
            if CONFIG["expire_after_pieces"] and piece > CONFIG["expire_after_pieces"]:
                TOKENS.discard("at-1")
                CONFIG["expire_after_pieces"] = None
                return self._reply(401, {"error": {"code": 401, "message": "Invalid Credentials", "errors": [{"reason": "authError"}]}})
            if CONFIG["fail_piece_once"] == piece and not SEEN["failed_once"]:
                SEEN["failed_once"] = True
                s["data"] += data[: len(data) // 3]  # (part of it got there)
                return self._reply(503, {"error": {"code": 503, "message": "Backend Error"}})
        if a != len(s["data"]):
            return self._reply(400, {"error": {"message": f"expected {len(s['data'])}, got {a}"}})
        s["data"] += data
        if len(s["data"]) < total:
            return self._reply(308, None, {"Range": f"bytes=0-{len(s['data']) - 1}"})
        self._done(s)

    def _done(self, s):
        vid = f"vid{len(SEEN['uploads']) + 1}"
        if not s.get("id"):
            s["id"] = vid
            SEEN["uploads"].append({"id": vid, "meta": s["meta"], "sha": hashlib.sha256(bytes(s["data"])).hexdigest(),
                                    "size": len(s["data"])})
        self._reply(201, {"id": s["id"], "snippet": s["meta"].get("snippet"), "status": s["meta"].get("status")})

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/youtube/v3/channels":
            if not self._authorized():
                return
            return self._reply(200, {"items": [{"id": "UC-test", "snippet": {"title": "Test Channel"}}]})
        if url.path == "/youtube/v3/playlists":
            if not self._authorized():
                return
            return self._reply(200, {"items": [{"id": k, "snippet": {"title": v}} for k, v in PLAYLISTS.items()]})
        self._reply(404, {})


def start(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
