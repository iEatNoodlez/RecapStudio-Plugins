"""New chapters on a schedule, a Recap Studio feature plugin: each project made from a source (Get chapters from a
site) is checked for new chapters on the days and at the time chosen, and what's new is got and, if the project says
so, recapped.

It gets nothing by itself: the check and the download go through the source the project's chapters came from, the
member's own (a plugin, gallery-dl or Suwayomi), as Check for new chapters and Get chapters do (sources.py).

What each project does with its new chapters is its own setting (new-chapters.when_new, in Project settings and in
this plugin's Settings section):
  off      not checked
  tell     checked, and the count shown, as the check when the app starts does
  get      got (a "Getting chapters" job), and nothing more
  recap    got, then recapped on their own: Make the recap for just them, so the video is of the new chapters
  all      got, then Make the recap for the whole project
When the app was closed at the time, it checks when it next opens. A project with a job running gets its chapters
once that's finished. Settings and what happened are kept in the plugin's data folder (settings.json, state.json).
"""
import json
import re
import threading
import time
from datetime import datetime, timedelta

from fastapi import Body, HTTPException

from recap import pipeline, sources, store

TICK = 60         # seconds between looks at the clock and at the chapters being got
LOG = 40          # what happened, kept for Settings
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CHOICES = ("off", "tell", "get", "recap", "all")
DEFAULTS = {"days": list(range(7)), "time": "06:00"}
FINISHED = ("done", "error", "cancelled")
_lock = threading.Lock()      # state.json
_round = threading.Lock()     # one check at a time
_plugin = None
_key = "new-chapters.when_new"


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
    return {**default, **found} if isinstance(found, dict) else json.loads(json.dumps(default))


def _write(name: str, data: dict) -> None:
    _write_json(_plugin.data_dir / name, data)


def _state() -> dict:
    return _read("state.json", {"last": None, "log": [], "pending": {}})


def _log(st: dict, project: str, note: str) -> None:
    st["log"].insert(0, {"at": time.time(), "project": project, "note": note})
    st["log"] = st["log"][:LOG]


def last_due(s: dict, now: float) -> float | None:
    """The latest scheduled moment at or before now (this PC's time), within the past week."""
    h, m = (int(x) for x in s["time"].split(":"))
    today = datetime.fromtimestamp(now)
    for back in range(8):
        day = today - timedelta(days=back)
        t = day.replace(hour=h, minute=m, second=0, microsecond=0)
        if day.weekday() in s["days"] and t.timestamp() <= now:
            return t.timestamp()
    return None


def next_due(s: dict, now: float) -> float | None:
    h, m = (int(x) for x in s["time"].split(":"))
    today = datetime.fromtimestamp(now)
    for ahead in range(8):
        day = today + timedelta(days=ahead)
        t = day.replace(hour=h, minute=m, second=0, microsecond=0)
        if day.weekday() in s["days"] and t.timestamp() > now:
            return t.timestamp()
    return None


def _choice(p: dict) -> str:
    c = p["settings"].get(_key) or "get"
    return c if c in CHOICES else "get"


def _busy(pid: str) -> bool:
    job = _plugin.job(pid)
    return bool(job and job["status"] in pipeline.ACTIVE)


def check_all(why: str = "") -> int:
    """Look for every project's new chapters now; start getting them where the project says to. Gives how many
    projects had new chapters."""
    with _round:
        with _lock:
            st = _state()
            st["last"], st["checking"] = time.time(), True
            _write("state.json", st)
        found_in = 0
        notes = []
        try:
            for entry in store.list_all():
                try:
                    p = store.load(entry["id"])
                except Exception:
                    continue
                if not p.get("source") or _choice(p) == "off":
                    continue
                try:
                    found = sources.new_chapters(p)
                except Exception as e:  # one source being down doesn't stop the others
                    notes.append((p["name"], f"couldn't check: {e}"))
                    continue
                new = found["series"]["chapters"]
                sources.new_counts[p["id"]] = len(new)
                if not new:
                    continue
                found_in += 1
                names = ", ".join(c["name"] for c in new[:4]) + (f" and {len(new) - 4} more" if len(new) > 4 else "")
                if _choice(p) == "tell":
                    notes.append((p["name"], f"{len(new)} new: {names}"))
                    continue
                with _lock:
                    st = _state()
                    if p["id"] in st["pending"]:  # (still getting the last ones)
                        continue
                    series = found["series"]
                    st["pending"][p["id"]] = {
                        "stage": "get", "then": _choice(p), "key": found["key"],
                        "series": {"ref": series["ref"], "url": series.get("url", ""), "title": series.get("title", "")},
                        "chapters": [{"ref": c["ref"], "name": c["name"], "url": c.get("url", "")} for c in new]}
                    _write("state.json", st)
                notes.append((p["name"], f"{len(new)} new: {names}; getting them"))
        finally:
            with _lock:
                st = _state()
                st["checking"] = False
                for name, note in reversed(notes):
                    _log(st, name, note)
                if not notes:
                    _log(st, "", f"No new chapters{why}")
                _write("state.json", st)
        advance()
        return found_in


def advance() -> None:
    """The chapters being got: start getting them once their project is free, then its recap when chosen."""
    with _lock:
        st = _state()
        for pid, it in list(st["pending"].items()):
            try:
                name = store.load(pid)["name"]
            except Exception:  # deleted
                st["pending"].pop(pid)
                continue
            if it["stage"] == "get":
                if _busy(pid):
                    continue
                job = pipeline.submit(pid, ["download"], options={
                    "key": it["key"], "series": it["series"], "chapters": it["chapters"], "place": "number", "skip": []})
                it.update(stage="getting", job=job["id"])
            elif it["stage"] == "getting":
                job = pipeline.jobs.get(it.get("job"))
                if job and job["status"] in pipeline.ACTIVE:
                    continue
                refs = {c["ref"] for c in it["chapters"]}
                got = [c["id"] for c in store.load(pid)["chapters"] if c.get("source_ref") in refs]
                if job and job["status"] != "done" and not got:
                    _log(st, name, "Getting the new chapters " + (f"failed: {job.get('error') or ''}"
                                                                  if job["status"] == "error" else "was cancelled"))
                    st["pending"].pop(pid)
                    continue
                if len(got) < len(refs):  # (stopped, or the app closed meanwhile: the rest are new next time)
                    _log(st, name, f"Got {len(got)} of {len(refs)} new chapters; the rest at the next check")
                else:
                    _log(st, name, f"Got {len(got)} new chapter{'s' * (len(got) != 1)}")
                if it["then"] in ("recap", "all") and got:
                    it.update(stage="recap", ids=got)
                else:
                    st["pending"].pop(pid)
            if it.get("stage") == "recap":
                if _busy(pid):
                    continue
                try:
                    if it["then"] == "recap":  # just the new chapters: their own video
                        pipeline.submit(pid, list(pipeline.STEPS), chapters=it["ids"], mode="missing")
                        _log(st, name, "Make the recap started for the new chapters")
                    else:
                        _plugin.make_recap(pid)
                        _log(st, name, "Make the recap started")
                except Exception as e:
                    _log(st, name, f"Make the recap didn't start: {e}")
                st["pending"].pop(pid)
        _write("state.json", st)


def tick() -> None:
    """Every minute: the schedule, then the chapters being got."""
    s, st = _read("settings.json", DEFAULTS), _state()
    if st["last"] is None:  # (just installed: from the next scheduled time)
        with _lock:
            st = _state()
            st["last"] = time.time()
            _write("state.json", st)
    due = last_due(s, time.time()) if s["days"] else None
    if due and st["last"] < due and not _round.locked():
        check_all()
    else:
        advance()


def _clock(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%a %d %b %H:%M")


def status() -> dict:
    s, st = _read("settings.json", DEFAULTS), _state()
    projects = []
    for entry in store.list_all():
        try:
            p = store.load(entry["id"])
        except Exception:
            continue
        if p.get("source"):
            it = st["pending"].get(p["id"])
            projects.append({"id": p["id"], "name": p["name"], "series": p["source"].get("title") or "",
                             "choice": _choice(p), "new": sources.new_counts.get(p["id"]),
                             "doing": {"get": "waiting for its job to finish", "getting": "getting chapters",
                                       "recap": "waiting to start its recap"}.get((it or {}).get("stage"), "")})
    nxt = next_due(s, time.time()) if s["days"] else None
    return {"settings": s, "days": DAYS, "projects": projects, "checking": bool(st.get("checking")) and _round.locked(),
            "last": _clock(st["last"]) if st.get("last") else None, "next": _clock(nxt) if nxt else None,
            "log": [{**x, "at": _clock(x["at"])} for x in st["log"]]}


def setup(plugin):
    global _plugin, _key
    _plugin = plugin
    _key = plugin.project_setting("when_new", "get")
    plugin.every(TICK, tick)
    plugin.on("job_done", lambda job: advance() if job["project"] in _state()["pending"] else None)

    @plugin.router.get("/status")
    def get_status():
        return status()

    @plugin.router.put("/settings")
    def save(days: list[int] = Body(..., embed=True), at: str = Body(..., embed=True)):
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
            raise HTTPException(400, f"{at} isn't a time (hh:mm)")
        with _lock:
            _write("settings.json", {"days": sorted({d for d in days if 0 <= d < 7}), "time": at})
            st = _state()
            st["last"] = time.time()  # (from the next scheduled time, not one already passed today)
            _write("state.json", st)
        return status()

    @plugin.router.post("/check")
    def check_now():
        if _round.locked():
            raise HTTPException(409, "It's checking already")
        threading.Thread(target=check_all, args=(" (checked by you)",), daemon=True, name="new-chapters").start()
        time.sleep(0.2)
        return status()
