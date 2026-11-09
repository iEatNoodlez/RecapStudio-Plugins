"""When a video is done, a Recap Studio feature plugin: what happens once a project's video is made (by Make the recap
or Render video), set in Settings:
- copy it to a folder (a folder per project, if you like), with its subtitles, thumbnail and Shorts when ticked;
- run a command of yours, with the video's path and the rest in it ({video} and so on) and in RECAP_* variables;
- tell your phone: through ntfy (a free app; this PC posts to a topic the phone follows) and/or a webhook (Discord's
  and Slack's take a message; any other gets the details as JSON: names, never this PC's paths).
The phone can also be told when a recap stops to ask you something (looked for every 30 seconds) or fails.

The copying and the command run on a thread of their own, one video after another, so the next job doesn't wait for
them. A project can be left out (when-done.use, in Project settings). Settings and what happened are kept in the
plugin's data folder (settings.json, log.json).
"""
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import requests
from fastapi import Body, HTTPException, Request

from recap import pipeline, store

LOG = 40
WATCH = 30                     # seconds between looks for recaps that stopped to ask
COMMAND_TIMEOUT = 3600         # seconds a command may run
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULTS = {
    "folder": "", "own_folder": True, "srt": True, "thumbnail": True, "shorts": False,
    "command": "",
    "ntfy_topic": "", "ntfy_server": "https://ntfy.sh", "webhook": "",
    "tell_done": True, "tell_asks": True, "tell_failed": True,
}
PLACES = ("video", "videos", "srt", "thumbnail", "folder", "project", "title", "copy")
_work: "queue.Queue[tuple]" = queue.Queue()
_lock = threading.Lock()
_told: set = set()             # (job id, reason) already told
_plugin = None
_key = "when-done.use"


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
    if found is None:
        return json.loads(json.dumps(default))
    return {**default, **found} if isinstance(default, dict) else found


def _write(name: str, data) -> None:
    _write_json(_plugin.data_dir / name, data)


def settings() -> dict:
    return _read("settings.json", DEFAULTS)


def _log(project: str, note: str) -> None:
    with _lock:
        log = _read("log.json", [])
        log.insert(0, {"at": time.time(), "project": project, "note": note})
        _write("log.json", log[:LOG])


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:100].strip(" .") or "project"


def _minutes(seconds: float) -> str:
    m = round(seconds / 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{max(1, m)} min"


# ---- telling the phone ---------------------------------------------------------------------------------------------

def tell(s: dict, title: str, message: str, details: dict) -> list[str]:
    """Send the message by ntfy and/or the webhook; gives what went wrong."""
    wrong = []
    if s["ntfy_topic"]:
        try:
            r = requests.post(s["ntfy_server"].rstrip("/"), timeout=20, json={  # (ntfy's JSON way: any language)
                "topic": s["ntfy_topic"], "title": title, "message": message, "tags": ["clapper"]})
            r.raise_for_status()
        except requests.RequestException as e:
            wrong.append(f"ntfy didn't take it ({e})")
    if s["webhook"]:
        hook = s["webhook"]
        body = ({"content": f"**{title}**\n{message}"} if re.match(r"https://(\w+\.)?discord(app)?\.com/api/webhooks/", hook)
                else {"text": f"*{title}*\n{message}"} if hook.startswith("https://hooks.slack.com/")
                else {"title": title, "text": message, **details})
        try:
            requests.post(hook, json=body, timeout=20).raise_for_status()
        except requests.RequestException as e:
            wrong.append(f"the webhook didn't take it ({e})")
    return wrong


def _tell(project: str, title: str, message: str, details: dict) -> None:
    s = settings()
    if not (s["ntfy_topic"] or s["webhook"]):
        return
    for problem in tell(s, title, message, details):
        _log(project, f"Couldn't tell your phone: {problem}")


# ---- what's done when a video is made ------------------------------------------------------------------------------

def outputs(p: dict) -> dict:
    """The project's finished video: its parts, subtitles, thumbnail and Shorts, as paths."""
    d = store.project_dir(p["id"])
    out = p.get("output") or {}
    parts = out.get("parts") or ([{"video": out["video"], "srt": out.get("srt"), "seconds": 0}] if out.get("video") else [])
    thumb = d / ((p.get("youtube") or {}).get("file") or "output/thumbnail.jpg")
    return {"videos": [d / x["video"] for x in parts if (d / x["video"]).exists()],
            "srts": [d / x["srt"] for x in parts if x.get("srt") and (d / x["srt"]).exists()],
            "seconds": sum(float(x.get("seconds") or 0) for x in parts),
            "thumbnail": thumb if thumb.exists() else None,
            "shorts": [d / x for x in out.get("shorts") or [] if (d / x).exists()],
            "folder": d / "output", "title": (p.get("youtube") or {}).get("title") or p["name"]}


def _copy(src: Path, dest: Path) -> Path:
    """Copied under a passing name, then renamed, so nothing watching the folder takes a half-copied file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".copying")
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)
    return dest


def copy_out(p: dict, o: dict, s: dict) -> tuple[Path, list[Path]]:
    dest = Path(s["folder"]) / (_safe(p["name"]) if s["own_folder"] else "")
    copied = [_copy(v, dest / v.name) for v in o["videos"]]
    if s["srt"]:
        copied += [_copy(x, dest / x.name) for x in o["srts"]]
    if s["thumbnail"] and o["thumbnail"]:
        stem = o["videos"][0].stem if o["videos"] else "recap"
        copied.append(_copy(o["thumbnail"], dest / f"{stem} - thumbnail{o['thumbnail'].suffix}"))
    if s["shorts"]:
        copied += [_copy(x, dest / "Shorts" / x.name) for x in o["shorts"]]
    return dest, copied


def _quote(value) -> str:
    """A value as one word for the shell, which nothing in it can break out of: in double quotes on Windows (cmd.exe
    has no way to quote a " inside them, so it's left out), and in shlex's quotes elsewhere, where double quotes
    would still run a title's $(…) or `…`."""
    value = str(value)
    if os.name == "nt":
        return '"' + value.replace('"', "") + '"'
    return shlex.quote(value)


def _on_server() -> bool:
    """Recap Studio as a server (0.4 and later): its page comes from its own address, not 127.0.0.1."""
    try:
        from recap import system
    except ImportError:
        return False
    return bool(getattr(system, "SERVER", False))


def command_line(command: str, values: dict) -> str:
    """The command with its {places} filled in, each quoted (a place already in quotes isn't quoted twice)."""
    for k in PLACES:
        v = values.get(k, "")
        quoted = " ".join(_quote(x) for x in v) if isinstance(v, list) else _quote(v)
        for wrapped in (f'"{{{k}}}"', f"'{{{k}}}'"):
            command = command.replace(wrapped, quoted)
        command = command.replace(f"{{{k}}}", quoted)
    return command


def run_command(command: str, values: dict, cwd: Path) -> str:
    env = {**os.environ, **{f"RECAP_{k.upper()}": (os.pathsep.join(v) if isinstance(v, list) else str(v))
                            for k, v in values.items()}}
    try:
        r = subprocess.run(command_line(command, values), shell=True, cwd=str(cwd), env=env, capture_output=True,
                           text=True, errors="replace", timeout=COMMAND_TIMEOUT, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return f"The command was stopped after {COMMAND_TIMEOUT // 60} minutes"
    last = ((r.stdout or "").strip().splitlines() or (r.stderr or "").strip().splitlines() or [""])[-1][:200]
    return (f"The command ran" if r.returncode == 0 else f"The command failed (exit code {r.returncode})") + (
        f": {last}" if last else "")


def video_done(pid: str) -> None:
    """Everything chosen, for a project whose video was just made (on the worker thread)."""
    p = store.load(pid)
    o, s = outputs(p), settings()
    if not o["videos"]:
        return _log(p["name"], "No video found to copy or tell about")
    notes, copy, copied = [], "", []
    if s["folder"]:
        try:
            where, copied = copy_out(p, o, s)
            copy = str(where / o["videos"][0].name)
            notes.append(f"Copied {len(copied)} file{'s' * (len(copied) != 1)} to {where}")
        except OSError as e:
            notes.append(f"Couldn't copy it: {e}")
    if s["command"]:
        values = {"video": str(o["videos"][0]), "videos": [str(v) for v in o["videos"]],
                  "srt": str(o["srts"][0]) if o["srts"] else "", "thumbnail": str(o["thumbnail"] or ""),
                  "folder": str(o["folder"]), "project": p["name"], "title": o["title"], "copy": copy}
        notes.append(run_command(s["command"], values, o["folder"]))
    for n in notes:
        _log(p["name"], n)
    if s["tell_done"]:
        parts = f" in {len(o['videos'])} parts" if len(o["videos"]) > 1 else ""
        problems = [x for x in notes if x.startswith(("Couldn't", "The command failed", "The command was stopped"))]
        _tell(p["name"], "Recap Studio: a video is done",
              f"{p['name']}: its video is done ({_minutes(o['seconds'])}{parts})."
              + "".join(f" {x}." for x in problems),
              {"event": "video_done", "project": p["name"], "files": [v.name for v in o["videos"]],
               "minutes": round(o["seconds"] / 60, 1)})


def _worker() -> None:
    while True:
        what, pid = _work.get()
        try:
            if what == "video":
                video_done(pid)
        except Exception as e:  # (one project's trouble doesn't stop the next)
            _log(pid, f"Something went wrong: {e}")


def _wanted(pid: str) -> bool:
    try:
        return store.load(pid)["settings"].get(_key, "yes") != "no"
    except Exception:
        return False


def after_job(job: dict) -> None:
    pid = job["project"]
    if not _wanted(pid):
        return
    if job["status"] == "done" and "video" in job["steps"]:
        _work.put(("video", pid))
    elif job["status"] == "error" and settings()["tell_failed"]:
        name = store.load(pid)["name"]
        step = pipeline.STEP_LABELS.get(job.get("step"), job.get("step") or "")
        _tell(name, "Recap Studio: a recap failed", f"{name}: {step} failed: {job.get('error') or ''}".strip(),
              {"event": "failed", "project": name, "step": step, "error": job.get("error") or ""})


def look() -> None:
    """Every 30 seconds: recaps that stopped to ask you something, told once each."""
    if not settings()["tell_asks"]:
        return
    for job in list(pipeline.jobs.values()):
        reason = job.get("pause_reason")
        if job["status"] in pipeline.ACTIVE and job.get("pause") and reason and (job["id"], reason) not in _told:
            _told.add((job["id"], reason))
            if _wanted(job["project"]):
                name = store.load(job["project"])["name"]
                _tell(name, "Recap Studio: a recap is waiting for you", f"{name}: {reason}",
                      {"event": "asks", "project": name, "question": reason})


def _status() -> dict:
    clock = lambda t: datetime.fromtimestamp(t).strftime("%d %b %H:%M")  # noqa: E731
    return {"settings": settings(), "places": list(PLACES), "windows": os.name == "nt",
            "log": [{**x, "at": clock(x["at"])} for x in _read("log.json", [])]}


def setup(plugin):
    global _plugin, _key
    _plugin = plugin
    _key = plugin.project_setting("use", "yes")
    plugin.on("job_done", after_job)
    plugin.every(WATCH, look)
    threading.Thread(target=_worker, daemon=True, name="when-done").start()

    @plugin.router.get("/status")
    def status():
        return _status()

    @plugin.router.put("/settings")
    def save(request: Request, changes: dict = Body(..., embed=True)):
        # the command runs on this PC: only the app's own page may set it, never another web page (it sends an
        # Origin). A server's page is its own address, behind its login; a reverse proxy passes the address the
        # browser used as Host (or X-Forwarded-Host)
        origin = request.headers.get("origin")
        own = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")} - {""}
        if origin and not re.match(r"https?://(127\.0\.0\.1|localhost)(:\d+)?$", origin) and not (
                _on_server() and urllib.parse.urlparse(origin).netloc in own):
            raise HTTPException(403, "Not from a web page")
        s = settings()
        for k, v in changes.items():
            if k in DEFAULTS:
                s[k] = bool(v) if isinstance(DEFAULTS[k], bool) else str(v).strip()
        if s["folder"]:
            s["folder"] = s["folder"].strip('"')
            if not Path(s["folder"]).is_absolute():
                raise HTTPException(400, f"{s['folder']} isn't a full path (D:\\Recaps, say)")
        for k in ("ntfy_server", "webhook"):
            if s[k] and not re.match(r"https?://\S+$", s[k]):
                raise HTTPException(400, f"{s[k]} isn't a web address")
        if s["ntfy_topic"] and not re.fullmatch(r"[\w-]{1,64}", s["ntfy_topic"]):
            raise HTTPException(400, "A topic is letters, digits, - and _ (up to 64)")
        _write("settings.json", s)
        return _status()

    @plugin.router.post("/test")
    def test():
        s = settings()
        if not (s["ntfy_topic"] or s["webhook"]):
            raise HTTPException(400, "Set an ntfy topic or a webhook first")
        wrong = tell(s, "Recap Studio", "This is a test: when a video is done, you'll be told here.",
                     {"event": "test", "project": ""})
        if wrong:
            raise HTTPException(502, "; ".join(wrong))
        return {"ok": True}

    @plugin.router.post("/now/{pid}")
    def now(pid: str):
        """Do it now for a project's video (the Video tab's button)."""
        try:
            p = store.load(pid)
        except Exception:
            raise HTTPException(404, "No such project")
        if not outputs(p)["videos"]:
            raise HTTPException(400, "This project has no video yet")
        _work.put(("video", pid))
        return {"queued": True}
