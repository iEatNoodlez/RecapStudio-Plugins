"""Watched folder, a Recap Studio feature plugin: chapters dropped into a folder are added to their project and, when
chosen, recapped.

The watched folder holds a folder per project, named after it (or after the project's own folder):
  <watched folder>\\<project name>\\Chapter 12\\       pictures
  <watched folder>\\<project name>\\Chapter 13.cbz     or .zip, or a file a plugin reads (an .epub: a chapter each part)
Every WATCH seconds each of those is scanned as Add chapters scans a folder (importer.scan), for chapters the project
doesn't have yet (the same check: by where a chapter came from, or by its name). A chapter is added once it has
stayed the same for SETTLE seconds, so one still being copied waits. Nothing is moved or deleted: chapters made of
pictures are read from where they are, as with Add chapters, so they have to stay there. A folder named after no
project makes a new one (with the last project's choices) only when that's switched on.

Settings and what happened are kept in the plugin's data folder (settings.json, state.json).
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import Body, HTTPException

from recap import importer, pipeline, store

WATCH = 30      # seconds between looks
SETTLE = 20     # seconds a new chapter has to stay the same before it's added
LOG = 30        # what happened, kept for Settings
DEFAULTS = {"folder": "", "recap": True, "create": False}
_lock = threading.Lock()
_plugin = None


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


def _read(name: str, default: dict) -> dict:
    found = _read_json(_plugin.data_dir / name)
    return {**default, **found} if isinstance(found, dict) else dict(default)


def _write(name: str, data: dict) -> None:
    _write_json(_plugin.data_dir / name, data)


def _signature(c: dict) -> list:
    """What changes while a chapter is still being copied: its files' count, sizes and times."""
    paths = [Path(c["path"]) / f for f in c["files"]] if c["kind"] == "folder" else [Path(c["path"])]
    stats = [p.stat() for p in paths if p.exists()]
    return [len(stats), sum(s.st_size for s in stats), max((s.st_mtime for s in stats), default=0)]


def _id(c: dict) -> str:
    """A chapter found in the folder: its folder or file, or its part of a file (an EPUB volume's chapter)."""
    return c.get("source", c["path"])


def _project_for(folder_name: str, projects: list[dict]) -> str | None:
    key = folder_name.strip().lower()
    for p in projects:
        if p["name"].strip().lower() == key or Path(p["folder"]).name.lower() == key:
            return p["id"]
    return None


def _busy(pid: str) -> bool:
    job = _plugin.job(pid)
    return bool(job and job["status"] in pipeline.ACTIVE)


def _will_do(pid: str) -> bool:
    """The project's Make the recap is waiting its turn (another project's job is running): it does the chapters
    added meanwhile too, since its steps look at the project when they start. Starting another after it would only
    join the video again (and tell When a video is done twice)."""
    job = _plugin.job(pid)
    return bool(job and job["status"] == "queued" and not job.get("chapters") and not job.get("only")
                and set(pipeline.STEPS) <= set(job["steps"]))


def look() -> dict:
    """One look at the watched folder: add what's settled, start Make the recap where chosen."""
    with _lock:
        s, st = _read("settings.json", DEFAULTS), _read("state.json", {"seen": {}, "log": [], "pending": []})
        now = time.time()
        st["last"], st["error"] = now, ""
        root = Path(s["folder"]) if s["folder"] else None
        if not root:
            st["error"] = "No folder chosen yet."
        elif not root.is_dir():
            st["error"] = f"{root} isn't there."
        else:
            projects = store.list_all()
            seen = {}
            for sub in sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")):
                pid = _project_for(sub.name, projects)
                if not pid and not s["create"]:
                    continue
                try:
                    found = importer.scan(str(sub))
                except (OSError, FileNotFoundError):
                    continue
                if pid:
                    p = store.load(pid)
                    have = {c.get("source") for c in p["chapters"]} | {c["name"] for c in p["chapters"]}
                    found = [c for c in found if _id(c) not in have and c["name"] not in have]
                ready = []
                for c in found:
                    sig, before = _signature(c), st["seen"].get(_id(c))
                    since = before[1] if before and before[0] == sig else now
                    seen[_id(c)] = [sig, since]
                    if now - since >= SETTLE:
                        ready.append(c)
                if not ready:
                    continue
                made = ""
                if not pid:
                    pid = store.create(sub.name, store.new_project_settings())["id"]
                    projects = store.list_all()
                    made = "a new project, "
                result = store.update(pid, lambda p: importer.import_into(p, str(sub), [c["key"] for c in ready], "number"))
                for c in ready:
                    seen.pop(_id(c), None)
                twice = result.get("duplicates") or []
                st["log"].insert(0, {"at": now, "project": store.load(pid)["name"],
                                     "note": f"{made}added {', '.join(c['name'] for c in ready)} "
                                             f"({result.get('pages', 0)} pages)"
                                             + (f"; {len(twice)} look imported twice" if twice else "")})
                if s["recap"] and pid not in st["pending"] and not _will_do(pid):
                    st["pending"].append(pid)
            st["seen"] = seen  # (what's gone from the folder is forgotten)
        for pid in list(st["pending"]):  # Make the recap, once the project has no job running
            try:
                if _busy(pid):
                    continue
                _plugin.make_recap(pid)
                st["log"].insert(0, {"at": now, "project": store.load(pid)["name"], "note": "Make the recap started"})
            except Exception as e:
                st["log"].insert(0, {"at": now, "project": pid, "note": f"Make the recap didn't start: {e}"})
            st["pending"].remove(pid)
        st["log"] = st["log"][:LOG]
        _write("state.json", st)
        return st


def _status() -> dict:
    s, st = _read("settings.json", DEFAULTS), _read("state.json", {"seen": {}, "log": [], "pending": []})
    clock = lambda t: datetime.fromtimestamp(t).strftime("%d %b %H:%M")  # noqa: E731
    return {"settings": s, "last": clock(st["last"]) if st.get("last") else None, "error": st.get("error") or "",
            "waiting": len(st.get("seen") or {}), "log": [{**x, "at": clock(x["at"])} for x in st.get("log") or []]}


def setup(plugin):
    global _plugin
    _plugin = plugin
    plugin.every(WATCH, look)

    @plugin.router.get("/status")
    def status():
        return _status()

    @plugin.router.put("/settings")
    def save(folder: str = Body("", embed=True), recap: bool = Body(True, embed=True), create: bool = Body(False, embed=True)):
        folder = folder.strip().strip('"')
        if folder:
            path = Path(folder)
            if not path.is_absolute() or not path.is_dir():
                raise HTTPException(400, f"{folder} isn't a folder on this PC (give its full path)")
            inside = store.projects_root().resolve()
            if path.resolve() == inside or inside in path.resolve().parents or path.resolve() in inside.parents:
                raise HTTPException(400, "Choose a folder of its own, not the projects folder or one around it")
            folder = str(path)
        with _lock:
            _write("settings.json", {"folder": folder, "recap": recap, "create": create})
        return _status()

    @plugin.router.post("/look")
    def look_now():
        look()
        return _status()
