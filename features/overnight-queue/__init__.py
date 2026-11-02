"""Overnight queue, a Recap Studio feature plugin: several projects' Make the recap, one after another, starting now
or at a time you choose, and stopping at a time you choose.

A project that stops to ask you something (names to check, the scan group's pages, a problem with a service) holds
the app's one job worker, so by default it's left for the morning: its job is cancelled where it waited (nothing it
did is lost: Make the recap carries on from there next time) and the next project starts. "Wait for me" keeps it
waiting instead. At the stop time the project running then is paused, to be resumed in the morning.

The queue is kept in queue.json in the plugin's data folder, so it carries on after the app restarts (the project
that was running starts again where it stopped).
"""
import json
import re
import threading
import time
from datetime import datetime, timedelta

from fastapi import Body, HTTPException

from recap import pipeline, store

TICK = 5          # seconds between looks at the queue
FINISHED = ("done", "error", "cancelled")
_lock = threading.Lock()
_plugin = None


def _file():
    return _plugin.data_dir / "queue.json"


def _load() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "idle", "items": []}


def _save(q: dict) -> None:
    _file().write_text(json.dumps(q, indent=1), encoding="utf-8")


def _at(hhmm: str, after: float) -> float:
    """The first moment at hh:mm (this PC's time) after a given time."""
    h, m = (int(x) for x in hhmm.split(":"))
    t = datetime.fromtimestamp(after).replace(hour=h, minute=m, second=0, microsecond=0)
    if t.timestamp() <= after:
        t += timedelta(days=1)
    return t.timestamp()


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _finish(item: dict, job: dict) -> None:
    item["ended"] = time.time()
    if job["status"] == "done":
        item.update(status="done", note=job.get("message") or "Finished")
    elif job["status"] == "error":
        item.update(status="failed", note=job.get("error") or job.get("message") or "It failed")
    elif item.get("asked"):
        item.update(status="left")  # (note: what it stopped to ask)
    else:
        item.update(status="cancelled", note="Cancelled")


def tick() -> None:
    """Look at the queue: note a finished project, step past one that's waiting for you, stop at the stop time,
    start the next."""
    with _lock:
        q = _load()
        if q["state"] not in ("waiting", "running"):
            return
        now = time.time()
        if q["state"] == "waiting":
            if now < q["start"]:
                return
            q["state"] = "running"
        current = next((it for it in q["items"] if it["status"] == "running"), None)
        if current:
            job = _plugin.job(current["project"])
            if not job or job["id"] != current.get("job"):
                if not job or job["status"] not in pipeline.ACTIVE:  # the app restarted: run it again
                    current["status"] = "waiting"
                    current = None
            elif job["status"] in FINISHED:
                _finish(current, job)
                current = None
            elif (job["status"] == "paused" and job.get("pause_reason") and q.get("on_ask") != "wait"
                  and not current.get("asked")):
                current.update(asked=True, note=job["pause_reason"])
                pipeline.jobs[job["id"]]["cancel"] = True  # finished on the next look
        if q.get("stop") and now >= q["stop"]:
            if current:
                live = pipeline.jobs.get(current.get("job"))
                if live and live["status"] in pipeline.ACTIVE:
                    live.update(pause=True, pause_reason=f"Paused by the overnight queue at {_clock(q['stop'])}: "
                                                         "Resume to carry on.")
                current.update(status="paused", note=f"Paused at {_clock(q['stop'])}")
            q.update(state="stopped", ended=now, why=f"Stopped at {_clock(q['stop'])}")
            _save(q)
            return
        if not current:
            for item in (it for it in q["items"] if it["status"] == "waiting"):
                try:
                    store.load(item["project"])
                except Exception:
                    item.update(status="failed", note="The project isn't there any more", ended=now)
                    continue
                job = _plugin.job(item["project"])
                if job and job["status"] in pipeline.ACTIVE:
                    break  # its own job is running (started by hand): wait for it
                job = _plugin.make_recap(item["project"])
                item.update(status="running", job=job["id"], started=now, note="")
                break
            else:
                if all(it["status"] != "waiting" for it in q["items"]):
                    q.update(state="done", ended=now, why="Every project had its turn")
        _save(q)


def _state() -> dict:
    """The queue for the page, with each project's name and the running one's progress."""
    q = _load()
    names = {p["id"]: p["name"] for p in store.list_all()}
    for item in q["items"]:
        item["name"] = names.get(item["project"], "(deleted)")
        if item["status"] == "running":
            job = _plugin.job(item["project"]) or {}
            item["live"] = {k: job.get(k) for k in ("status", "step", "message", "progress")}
    for key in ("start", "stop", "ended"):
        if q.get(key):
            q[f"{key}_clock"] = _clock(q[key])
    return q


def setup(plugin):
    global _plugin
    _plugin = plugin
    plugin.every(TICK, tick)
    plugin.on("job_done", lambda job: tick())

    @plugin.router.get("/state")
    def state():
        return _state()

    @plugin.router.post("/start")
    def start(projects: list[str] = Body(..., embed=True), start_at: str | None = Body(None, embed=True),
              stop_at: str | None = Body(None, embed=True), on_ask: str = Body("skip", embed=True)):
        for t in (start_at, stop_at):
            if t and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
                raise HTTPException(400, f"{t} isn't a time (hh:mm)")
        known = {p["id"] for p in store.list_all()}
        projects = [p for p in dict.fromkeys(projects) if p in known]
        if not projects:
            raise HTTPException(400, "Tick at least one project")
        with _lock:
            if _load()["state"] in ("waiting", "running"):
                raise HTTPException(409, "The queue is already on: stop it first")
            now = time.time()
            begin = _at(start_at, now) if start_at else now
            _save({"state": "waiting", "start": begin, "stop": _at(stop_at, begin) if stop_at else None,
                   "on_ask": "wait" if on_ask == "wait" else "skip", "created": now,
                   "items": [{"project": p, "status": "waiting", "note": ""} for p in projects]})
        tick()
        return _state()

    @plugin.router.post("/stop")
    def stop():
        """No other project starts; the one running goes on to the end of its job."""
        with _lock:
            q = _load()
            if q["state"] in ("waiting", "running"):
                for item in q["items"]:
                    if item["status"] == "running":
                        item.update(status="left running", note="Still running when the queue was stopped")
                q.update(state="stopped", ended=time.time(), why="Stopped by you")
                _save(q)
        return _state()

    @plugin.router.post("/clear")
    def clear():
        with _lock:
            if _load()["state"] in ("waiting", "running"):
                raise HTTPException(409, "Stop the queue first")
            _save({"state": "idle", "items": []})
        return _state()
