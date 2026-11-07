"""Autopilot, a Recap Studio feature plugin: sit back. On the days and at the time you choose it reads a chart of
your own sources (Popular, Trending, Hot: whatever lists they have), takes the series still coming out, gets their
newest chapters into projects (a new one for a series it hasn't seen, made with the last project's choices) and makes
the recaps, one series after another. With Post to YouTube connected, the videos it makes go up by themselves.

It gets nothing by itself: charts, series and chapters come through the sources the member added (a plugin, or
Suwayomi's sources; gallery-dl has no charts), as Get chapters does. A site that wants its check for a person
(Cloudflare) is left for the member: the Site check plugin passes it in a window, never this one.

A run (one at a time) plans, then flies:
  plan   each chart's first page; from the top, series not on the skip list, not from an NSFW source unless allowed,
         with a chapter dated within the last so many days (a series whose chapters carry no dates is taken: nothing
         says it's over). A series seen before gets its new chapters (at most so many), one never seen its newest few;
         so many chapters a run in all.
  fly    series by series: the project (found by its source, or made), a Getting chapters job, then Make the recap for
         the new chapters (their own video) or for the whole project. The app has one job worker, so a project with a
         job of its own waits for it. A job that stops to ask something (a key, credit, the wording check) ends the
         run; the chapters got by then are recapped first thing next time.
Settings, the series it has seen, the run and its log are kept in the plugin's data folder (settings.json,
state.json). Nothing is ever deleted: the projects it makes are projects like any other.
"""
import json
import re
import threading
import time
import urllib.parse
from datetime import datetime, timedelta

from fastapi import Body, HTTPException, Request

from recap import pipeline, sources, store
from recap import plugins as plugins_

TICK = 60         # seconds between looks at the clock and at the run
LOG = 60          # what happened, kept for the page
RUNS = 12         # past runs' digests kept
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULTS = {"on": False, "days": list(range(7)), "time": "03:00", "charts": [], "top": 5, "fresh_days": 45, "first": 2,
            "each": 5, "budget": 10, "nsfw": False, "skip": [], "recap": "new", "youtube": False, "privacy": "private"}
LIMITS = {"top": (1, 50), "fresh_days": (0, 365), "first": (1, 20), "each": (1, 20), "budget": (1, 100)}
KINDS = {"webtoon": {"layout": "webtoon", "reading_direction": "ltr"}, "manga": {"layout": "pages", "reading_direction": "rtl"},
         "comic": {"layout": "pages", "reading_direction": "ltr"}, "": {"layout": "auto", "reading_direction": "ltr"}}
FINISHED = ("done", "error", "cancelled")
OVER = ("done", "failed", "left", "skipped")  # an entry of a run that's had its turn
_lock = threading.Lock()      # state.json
_round = threading.Lock()     # one run at a time
_plugin = None


def _read(name: str, default: dict) -> dict:
    try:
        return {**default, **json.loads((_plugin.data_dir / name).read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return json.loads(json.dumps(default))


def _write(name: str, data: dict) -> None:
    (_plugin.data_dir / name).write_text(json.dumps(data, indent=1), encoding="utf-8")


def _settings() -> dict:
    return _read("settings.json", DEFAULTS)


def _state() -> dict:
    return _read("state.json", {"last": None, "log": [], "series": {}, "run": None, "lineup": None, "runs": [],
                                "leftover": []})


def _log(st: dict, note: str, series: str = "") -> None:
    st["log"].insert(0, {"at": time.time(), "series": series, "note": note})
    st["log"] = st["log"][:LOG]


def _on_server() -> bool:
    try:
        from recap import system
    except ImportError:
        return False
    return bool(getattr(system, "SERVER", False))


# ---- the clock ----

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


def _clock(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%a %d %b %H:%M")


_AGO = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400, "week": 7 * 86400,
        "month": 30 * 86400, "year": 365 * 86400}
_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y",
            "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%d.%m.%Y", "%d-%m-%Y", "%b %d", "%B %d")


def when(text: str, now: float | None = None) -> float | None:
    """A chapter's date as sources write them ("2026-11-07", "Nov 7, 2026", "3 days ago", "yesterday"), as a time;
    None when it can't be read."""
    now = time.time() if now is None else now
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return None
    low = t.lower()
    if low in ("just now", "now", "today", "new"):
        return now
    if low == "yesterday":
        return now - 86400
    m = re.match(r"(?:about |over |almost )?(\d+|an?) ?([a-z]+?)s? ago$", low)
    if m and m.group(2) in _AGO:
        n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
        return now - n * _AGO[m.group(2)]
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    plain = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)
    for fmt in _FORMATS:
        try:
            d = datetime.strptime(plain, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:  # "Nov 7": this year, or last year when that would be in the future
            d = d.replace(year=datetime.fromtimestamp(now).year)
            if d.timestamp() > now + 86400:
                d = d.replace(year=d.year - 1)
        return d.timestamp()
    return None


def fresh(chapters: list[dict], days: int, now: float | None = None) -> bool:
    """Whether a series is still coming out: a chapter dated within the last so many days. Judged by the newest
    chapters' dates; chapters with no dates that can be read leave it in (nothing says it's over)."""
    if days <= 0:
        return True
    now = time.time() if now is None else now
    known = [t for t in (when(c.get("date") or "", now) for c in chapters[-3:]) if t is not None]
    return not known or max(known) >= now - days * 86400


# ---- YouTube, through Post to YouTube ----

def youtube_state() -> dict:
    """Whether Post to YouTube is on and a channel is connected (its token.json in its data folder)."""
    run = plugins_.loaded.get("youtube-post")
    installed = bool(run) and not run.get("error")
    connected = False
    if installed:
        try:
            token = json.loads((_plugin.data_dir.parent / "youtube-post" / "token.json").read_text(encoding="utf-8"))
            connected = bool(token.get("refresh_token"))
        except (OSError, ValueError):
            pass
    return {"installed": installed, "connected": connected}


def _post_to_youtube(pid: str, privacy: str) -> bool:
    """Tell Post to YouTube to post this project's video when it's made (its choice for the project, which it reads
    from its projects.json each time)."""
    folder = _plugin.data_dir.parent / "youtube-post"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "projects.json"
        try:
            choices = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            choices = {}
        choices[pid] = {**choices.get(pid, {}), "when": "auto", "privacy": privacy}
        path.write_text(json.dumps(choices, indent=1), encoding="utf-8")
        return True
    except OSError:
        return False


# ---- the plan ----

def _skipped(title: str, skip: list[str]) -> bool:
    low = title.lower()
    return any(s.lower() in low for s in skip if s.strip())


def _find_project(key: str, ref: str, st: dict) -> str | None:
    """The project of a series: the one this plugin made or used, else any project got from the same source."""
    seen = st["series"].get(f"{key}|{ref}")
    if seen:
        try:
            store.load(seen["pid"])
            return seen["pid"]
        except Exception:  # deleted since
            pass
    for entry in store.list_all():
        try:
            src = store.load(entry["id"]).get("source") or {}
        except Exception:
            continue
        if src.get("key") == key and src.get("ref") == ref:
            return entry["id"]
    return None


def plan(s: dict, st: dict) -> tuple[list[dict], list[str]]:
    """What a run would do now: the series from each chart's top, each with the chapters to get. With notes on
    what was passed over and why (one chart or series failing doesn't stop the others)."""
    entries, notes, taken = [], [], 0
    byname = {a["key"]: a for a in sources.available()}
    for it in st.get("leftover") or []:
        try:
            title = store.load(it["pid"])["name"]
        except Exception:
            continue
        entries.append({"key": it.get("key", ""), "source": it.get("source", ""), "series": None, "kind": "", "pid": it["pid"],
                        "title": title, "chapters": [], "ids": it["ids"], "status": "waiting", "note": "left from last time",
                        "new": False})
    for chart in s["charts"]:
        key, list_id = chart.get("key", ""), chart.get("list", "")
        src = byname.get(key)
        if not src:
            notes.append(f"{key}: that source isn't there any more")
            continue
        if src.get("nsfw") and not s["nsfw"]:
            notes.append(f"{src['name']}: an NSFW source, left out (Settings allows them)")
            continue
        lname = next((x["name"] for x in src.get("lists", []) if x["id"] == list_id), list_id)
        try:
            items = sources.browse(key, list_id, 1)["items"]
        except sources.SiteCheck as e:
            notes.append(f"{src['name']}: {e.check['host']} wants its check for a person: pass it in the app (Site check)")
            continue
        except Exception as e:
            notes.append(f"{src['name']} ({lname}): {e}")
            continue
        found = 0
        for item in items:
            if found >= s["top"] or taken >= s["budget"]:
                break
            title = item["title"]
            if _skipped(title, s["skip"]):
                notes.append(f"{title}: on the skip list")
                continue
            try:
                series = sources.series(key, item["ref"])
            except sources.SiteCheck as e:
                notes.append(f"{title}: {e.check['host']} wants its check for a person: pass it in the app (Site check)")
                continue
            except Exception as e:
                notes.append(f"{title}: {e}")
                continue
            chapters = series["chapters"]
            if not chapters:
                notes.append(f"{title}: no chapters listed")
                continue
            if not fresh(chapters, s["fresh_days"]):
                notes.append(f"{title}: nothing new in {s['fresh_days']} days, so finished or stalled")
                continue
            pid = _find_project(key, item["ref"], st)
            if pid:
                try:
                    have = {c.get("source_ref") for c in store.load(pid)["chapters"]}
                    skipped = set((store.load(pid).get("source") or {}).get("skipped") or [])
                except Exception:
                    have, skipped = set(), set()
                new = [c for c in chapters if c["ref"] not in have and c["ref"] not in skipped]
                if have and any(c["ref"] in have for c in chapters):  # (newer than what it has, not the gaps before)
                    last = max(i for i, c in enumerate(chapters) if c["ref"] in have)
                    new = [c for i, c in enumerate(chapters) if i > last and c["ref"] not in skipped]
                if any(e["pid"] == pid for e in entries):
                    continue  # (a leftover of it is in the plan already)
                want = new[-s["each"]:]
                if not want:
                    notes.append(f"{title}: no new chapters")
                    continue
            else:
                want = chapters[-s["first"]:]
            want = want[:max(0, s["budget"] - taken)]
            if not want:
                break
            taken += len(want)
            found += 1
            entries.append({"key": key, "source": src["name"], "series": {"ref": series["ref"], "url": series.get("url", ""),
                                                                           "title": series.get("title") or title},
                            "kind": series.get("kind") or item.get("kind") or "", "pid": pid, "title": series.get("title") or title,
                            "chapters": [{"ref": c["ref"], "name": c["name"], "url": c.get("url", "")} for c in want],
                            "ids": [], "status": "waiting", "note": "", "new": pid is None})
    return entries, notes


# ---- the run ----

def _busy(pid: str) -> bool:
    job = _plugin.job(pid)
    return bool(job and job["status"] in pipeline.ACTIVE)


def _make_project(entry: dict, s: dict, st: dict) -> str:
    settings = {**store.new_project_settings(), **KINDS.get(entry["kind"], KINDS[""])}
    p = store.create(entry["title"], settings)
    pid = p["id"]
    store.update(pid, lambda x: x["settings"].update({"credits_review": "trust", "characters_review": "trust"}))
    st["series"][f"{entry['key']}|{entry['series']['ref']}"] = {"pid": pid, "title": entry["title"], "first": time.time()}
    if s["youtube"] and youtube_state()["connected"]:
        if not _post_to_youtube(pid, s["privacy"]):
            _log(st, "Couldn't tell Post to YouTube about it", entry["title"])
    try:
        plugins_.emit("project_created", project=store.load(pid))
    except Exception:
        pass
    return pid


def _finish_run(st: dict) -> None:
    run = st["run"]
    made = sum(1 for e in run["plan"] if e["status"] == "done")
    got = sum(len(e.get("ids") or []) for e in run["plan"] if e["status"] != "waiting" and e.get("got"))
    failed = [e for e in run["plan"] if e["status"] == "failed"]
    left = [e for e in run["plan"] if e["status"] == "left"]
    skipped = sum(1 for e in run["plan"] if e["status"] == "skipped")
    parts = [f"{len(run['plan'])} series", f"{got} chapter{'s' * (got != 1)} got", f"{made} recap{'s' * (made != 1)} made"]
    if failed:
        parts.append(f"{len(failed)} failed")
    if left:
        parts.append(f"{len(left)} left for you")
    if skipped:
        parts.append(f"{skipped} not started")
    why = run.get("grounded") or ("stopped by you" if run.get("stop") else "")
    digest = {"started": run["started"], "ended": time.time(), "why": run["why"], "summary": ", ".join(parts),
              "grounded": why, "entries": [{k: e.get(k) for k in ("title", "status", "note", "new", "pid")} for e in run["plan"]],
              "notes": run.get("notes", [])}
    st["runs"] = [digest] + st["runs"][: RUNS - 1]
    _log(st, f"Run over ({run['why']}): {digest['summary']}" + (f". {why}" if why else ""))
    st["run"] = None


def advance() -> None:
    """The run: one series at a time (the app's one job worker), each through its project, its Getting chapters job
    and its recap."""
    with _lock:
        st = _state()
        run = st.get("run")
        if not run or run.get("stage") != "flying":
            return
        s = _settings()
        for entry in run["plan"]:
            if entry["status"] in OVER:
                continue
            if run.get("stop") and entry["status"] == "waiting":
                entry.update(status="skipped", note="the run was stopped")
                continue
            pid = entry.get("pid")
            if entry["status"] == "waiting":
                if not pid:
                    try:
                        pid = entry["pid"] = _make_project(entry, s, st)
                        _log(st, "Project made" + (" (posting to YouTube when its video is made)"
                                                   if s["youtube"] and youtube_state()["connected"] else ""), entry["title"])
                    except Exception as e:
                        entry.update(status="failed", note=f"the project couldn't be made: {e}")
                        continue
                try:
                    store.load(pid)
                except Exception:
                    entry.update(status="failed", note="the project isn't there any more")
                    continue
                if _busy(pid):
                    entry["note"] = "waiting for its own job to finish"
                    break
                if entry["chapters"]:
                    job = pipeline.submit(pid, ["download"], options={
                        "key": entry["key"], "series": entry["series"], "chapters": entry["chapters"], "place": "number", "skip": []})
                    entry.update(status="getting", job=job["id"], note="")
                    break
                entry.update(status="got", got=True)  # (left from last time: its chapters are there)
            if entry["status"] == "getting":
                job = pipeline.jobs.get(entry.get("job") or "")
                if job and job["status"] in pipeline.ACTIVE:
                    break
                if not job:  # the app closed meanwhile: get them again (a chapter already there isn't got twice)
                    entry.update(status="waiting", job=None)
                    break
                refs = {c["ref"] for c in entry["chapters"]}
                try:
                    ids = [c["id"] for c in store.load(pid)["chapters"] if c.get("source_ref") in refs]
                except Exception:
                    ids = []
                if not ids:
                    why = (job.get("error") or "") if job["status"] == "error" else "it was cancelled"
                    entry.update(status="failed", note=f"getting the chapters failed: {why}".rstrip(": "))
                    _log(st, entry["note"], entry["title"])
                    continue
                entry.update(status="got", ids=ids, got=True)
                _log(st, f"Got {len(ids)} chapter{'s' * (len(ids) != 1)}" + (f" of {len(refs)}" if len(ids) < len(refs) else ""),
                     entry["title"])
            if entry["status"] == "got":
                if run.get("stop"):  # its chapters are there: recapped first thing next time
                    entry.update(status="skipped", note="got its chapters; the run was stopped before its recap")
                    st["leftover"] = [x for x in st["leftover"] if x["pid"] != pid] + [
                        {"pid": pid, "ids": entry["ids"], "key": entry["key"], "source": entry["source"]}]
                    continue
                if _busy(pid):
                    break
                try:
                    if s["recap"] == "all":
                        job = _plugin.make_recap(pid)
                    else:
                        job = pipeline.submit(pid, list(pipeline.STEPS), chapters=entry["ids"], mode="missing")
                except Exception as e:
                    entry.update(status="failed", note=f"Make the recap didn't start: {e}")
                    _log(st, entry["note"], entry["title"])
                    continue
                entry.update(status="recapping", job=job["id"], note="")
                _log(st, "Make the recap started" + (" for the new chapters" if s["recap"] != "all" else ""), entry["title"])
                break
            if entry["status"] == "recapping":
                job = pipeline.jobs.get(entry.get("job") or "")
                if job and job["status"] in pipeline.ACTIVE:
                    if job["status"] == "paused" and job.get("pause_reason") and not entry.get("asked"):
                        entry.update(asked=True, note=job["pause_reason"])
                        job["cancel"] = True  # finished on the next look; nothing it did is lost
                    break
                if job and job["status"] == "done":
                    entry.update(status="done", note=job.get("message") or "Finished")
                    _log(st, "Recap made", entry["title"])
                    continue
                if entry.get("asked"):
                    entry["status"] = "left"
                    st["leftover"] = [x for x in st["leftover"] if x["pid"] != pid] + [
                        {"pid": pid, "ids": entry["ids"], "key": entry["key"], "source": entry["source"]}]
                    run["grounded"] = f"{entry['title']} stopped to ask: {entry['note']} The run ended there; its recap is first next time."
                    _log(st, run["grounded"], entry["title"])
                    for rest in run["plan"]:
                        if rest["status"] == "waiting":
                            rest.update(status="skipped", note="the run ended early")
                    break
                if not job:  # the app closed meanwhile: Make the recap again, from where it got to
                    entry.update(status="got", job=None)
                    break
                entry.update(status="failed", note=job.get("error") or job.get("message") or "the recap failed")
                _log(st, f"The recap failed: {entry['note']}", entry["title"])
                continue
        done_ids = {e["pid"] for e in run["plan"] if e["status"] in ("done", "failed") and e.get("pid")}
        st["leftover"] = [x for x in st["leftover"] if x["pid"] not in done_ids]
        if all(e["status"] in OVER for e in run["plan"]):
            _finish_run(st)
        _write("state.json", st)


def start_run(why: str, dry: bool = False) -> None:
    """Plan (asking the sources, which takes a while), then fly; a dry run only shows the plan."""
    if not _round.acquire(blocking=False):
        return
    try:
        s = _settings()
        with _lock:
            st = _state()
            if st.get("run"):
                return
            st["run"] = {"stage": "planning", "why": why, "started": time.time(), "dry": dry, "plan": [], "notes": []}
            if not dry:
                st["last"] = time.time()
            _write("state.json", st)
        try:
            entries, notes = plan(s, st)
        except Exception as e:  # (shouldn't happen: each chart's failure is a note)
            entries, notes = [], [f"Planning failed: {e}"]
        with _lock:
            st = _state()
            if dry:
                st["lineup"] = {"at": time.time(), "entries": entries, "notes": notes}
                st["run"] = None
                _log(st, f"Dry run: {len(entries)} series would be done" + (f", {len(notes)} passed over" if notes else ""))
            else:
                st["run"].update(stage="flying", plan=entries, notes=notes)
                st["lineup"] = {"at": time.time(), "entries": entries, "notes": notes}
                _log(st, f"Run started ({why}): {len(entries)} series, "
                         f"{sum(len(e['chapters']) for e in entries)} chapters to get" + (f"; {len(notes)} passed over" if notes else ""))
                if not entries:
                    _finish_run(st)
            _write("state.json", st)
    finally:
        _round.release()
    advance()


def tick() -> None:
    """Every minute: the schedule, then the run."""
    s, st = _settings(), _state()
    if st["last"] is None:  # (just installed: from the next scheduled time)
        with _lock:
            st = _state()
            st["last"] = time.time()
            _write("state.json", st)
        return
    due = last_due(s, time.time()) if s["days"] else None
    if s["on"] and s["charts"] and due and st["last"] < due and not st.get("run") and not _round.locked():
        threading.Thread(target=start_run, args=("scheduled",), daemon=True, name="autopilot").start()
    else:
        advance()


def _live(entry: dict) -> dict:
    out = dict(entry)
    if entry["status"] in ("getting", "recapping") and entry.get("job"):
        job = pipeline.jobs.get(entry["job"]) or {}
        out["live"] = {k: job.get(k) for k in ("status", "step", "message", "progress")}
    return out


def status() -> dict:
    s, st = _settings(), _state()
    charts = [{"key": a["key"], "name": a["name"], "nsfw": a.get("nsfw", False), "lists": a.get("lists") or []}
              for a in sources.available() if a.get("lists")]
    nxt = next_due(s, time.time()) if s["days"] and s["on"] else None
    run = st.get("run")
    if run:
        run = {**run, "plan": [_live(e) for e in run["plan"]], "started_clock": _clock(run["started"])}
    return {"settings": s, "days": DAYS, "charts": charts, "youtube": youtube_state(), "server": _on_server(),
            "next": _clock(nxt) if nxt else None, "last": _clock(st["runs"][0]["ended"]) if st["runs"] else None,
            "run": run, "lineup": st.get("lineup"), "runs": [{**r, "started_clock": _clock(r["started"])} for r in st["runs"]],
            "leftover": len(st.get("leftover") or []), "seen": len(st["series"]),
            "log": [{**x, "at": _clock(x["at"])} for x in st["log"]]}


def _own_page(request: Request) -> None:
    """Only the app's own page may change what it does: not another web page open in the same browser (its request
    carries that page's Origin). On a server, the page's own host counts as its own."""
    origin = request.headers.get("origin")
    own = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")} - {""}
    if origin and not re.match(r"https?://(127\.0\.0\.1|localhost)(:\d+)?$", origin) and not (
            _on_server() and urllib.parse.urlparse(origin).netloc in own):
        raise HTTPException(403, "Not from a web page")


def setup(plugin):
    global _plugin
    _plugin = plugin
    plugin.every(TICK, tick)
    plugin.on("job_done", lambda job: advance() if _state().get("run") else None)

    @plugin.router.get("/status")
    def get_status():
        return status()

    @plugin.router.put("/settings")
    def save(request: Request, settings: dict = Body(..., embed=True)):
        _own_page(request)
        s = _settings()
        given = settings if isinstance(settings, dict) else {}
        if "time" in given and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(given["time"])):
            raise HTTPException(400, f"{given['time']} isn't a time (hh:mm)")
        for k, (lo, hi) in LIMITS.items():
            if k in given:
                try:
                    given[k] = min(hi, max(lo, int(given[k])))
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{k} needs a number")
        if "days" in given:
            given["days"] = sorted({int(d) for d in given["days"] if isinstance(d, int) and 0 <= d < 7})
        if "charts" in given:
            given["charts"] = [{"key": str(c.get("key", ""))[:200], "list": str(c.get("list", ""))[:60]}
                               for c in given["charts"] if isinstance(c, dict) and c.get("key") and c.get("list")][:40]
        if "skip" in given:
            given["skip"] = [str(x).strip()[:80] for x in given["skip"] if str(x).strip()][:200]
        if "recap" in given and given["recap"] not in ("new", "all"):
            raise HTTPException(400, "recap: new or all")
        if "privacy" in given and given["privacy"] not in ("private", "unlisted", "public"):
            raise HTTPException(400, "privacy: private, unlisted or public")
        for k in ("on", "nsfw", "youtube"):
            if k in given:
                given[k] = bool(given[k])
        changed = {k: v for k, v in given.items() if k in DEFAULTS}
        with _lock:
            _write("settings.json", {**s, **changed})
            if {"days", "time", "on"} & set(changed):
                st = _state()
                st["last"] = time.time()  # (from the next scheduled time, not one already passed today)
                _write("state.json", st)
        return status()

    @plugin.router.post("/run")
    def run_now(request: Request, dry: bool = Body(False, embed=True)):
        _own_page(request)
        s = _settings()
        if not s["charts"]:
            raise HTTPException(400, "Pick a chart first (Settings)")
        if _state().get("run") or _round.locked():
            raise HTTPException(409, "A run is going already")
        threading.Thread(target=start_run, args=("started by you" if not dry else "dry run", dry), daemon=True,
                         name="autopilot").start()
        time.sleep(0.2)
        return status()

    @plugin.router.post("/stop")
    def stop(request: Request):
        """No other series starts; the one going on finishes its job."""
        _own_page(request)
        with _lock:
            st = _state()
            if st.get("run"):
                st["run"]["stop"] = True
                _write("state.json", st)
        advance()
        return status()
