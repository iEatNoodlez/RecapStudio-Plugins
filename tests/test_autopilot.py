"""Autopilot, against two made-up sources on this PC (source plugins with a Popular list and series with dated
chapters, pictures served on 127.0.0.1; one of them NSFW) and a stand-in for Make the recap's steps: reading chapters'
dates, the sieve (the top of the chart, the skip list, a series finished or stalled, an NSFW source, the budget), a dry
run, a run (projects made with the kind of comic and nothing to stop and ask, the newest chapters got, recaps of just
them, Post to YouTube told), a series seen before getting only what's new, a project made by hand found, a recap that
stops to ask ending the run and recapped first next time, Stop, the schedule, the settings, and changes refused from
another web page."""
import io
import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

from helpers import App, check, done, until


def png(text: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (400, 600), (len(text) * 7 % 255, 120, 200)).save(out, "PNG")
    return out.getvalue()


class Pictures(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        data = png(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


site = ThreadingHTTPServer(("127.0.0.1", 0), Pictures)
threading.Thread(target=site.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{site.server_address[1]}"

BEHAVIOUR = {}
app = App(plugins=["autopilot"])
app.fake_recap(BEHAVIOUR)
pipeline, plugins, store = app.pipeline, app.plugins, app.store

# the chart and the series, read by the sources each time, so the test changes them as a site would
WORLD = app.data / "made-up-world.json"
world = {"chart": ["alpha", "old", "skip me", "beta", "gamma"],
         "series": {"alpha": {"kind": "webtoon", "chapters": [[1, "3 days ago"], [2, "yesterday"]]},
                    "beta": {"kind": "manga", "chapters": [[1, "2026-01-05"], [2, "Nov 1, 2026"], [3, "today"]]},
                    "gamma": {"kind": "", "chapters": [[1, ""], [2, ""]]},
                    "old": {"kind": "manga", "chapters": [[1, "2019-03-02"], [2, "2019-09-14"]]},
                    "skip me": {"kind": "", "chapters": [[1, "today"]]}}}
WORLD.write_text(json.dumps(world), encoding="utf-8")
SOURCE = '''
import json
RECAP_PLUGIN = 1
NAME = "%s"
SITES = ["127.0.0.1"]
NSFW = %s
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
WORLD = %r

def _world():
    return json.loads(open(WORLD, encoding="utf-8").read())

def browse(list_id, page):
    w = _world()
    items = w["chart"] if list_id == "popular" else list(reversed(w["chart"]))
    return {"items": [{"url": s, "title": s.title(), "kind": w["series"][s].get("kind", "")} for s in items], "more": False}

def series(url):
    d = _world()["series"][url]
    return {"title": url.title(), "kind": d.get("kind", ""),
            "chapters": [{"url": f"{url}/{n}", "name": f"Chapter {n}", "number": n, "date": date} for n, date in d["chapters"]]}

def pages(chapter):
    return [f"%s/{chapter}/{i}.png" for i in range(2)]
'''
(app.data / "plugins" / "madeup.py").write_text(SOURCE % ("Made-up site", "False", str(WORLD), BASE), encoding="utf-8")
(app.data / "plugins" / "spicy.py").write_text(SOURCE % ("Spicy site", "True", str(WORLD), BASE), encoding="utf-8")

check("plugin loaded", app.loaded("autopilot"), plugins.loaded.get("autopilot"))
ap = app.module("autopilot")
API = "/api/plugins/autopilot"


def names(pid):
    return [c["name"] for c in store.load(pid)["chapters"]]


def jobs(pid):
    return sorted((j for j in pipeline.jobs.values() if j["project"] == pid), key=lambda j: j["created"])


def by_name(name):
    return next((p["id"] for p in store.list_all() if p["name"] == name), None)


def run_over():
    return until(lambda: ap._state().get("run") is None, 60)


# ---- dates ----
now = datetime(2026, 11, 7, 12, 0).timestamp()
day = 86400
check("dates as sources write them", [round((now - (ap.when(t, now) or 0)) / day, 2) for t in
      ("2026-11-07", "2026-11-05T12:00:00", "Nov 6, 2026", "6 November 2026", "November 6th, 2026", "3 days ago",
       "an hour ago", "yesterday", "today", "2 weeks ago")] == [0.5, 2.0, 1.5, 1.5, 1.5, 3.0, 0.04, 1.0, 0.0, 14.0],
      [round((now - (ap.when(t, now) or 0)) / day, 2) for t in ("2026-11-07", "2026-11-05T12:00:00", "Nov 6, 2026", "6 November 2026",
                                                                "November 6th, 2026", "3 days ago", "an hour ago", "yesterday", "today", "2 weeks ago")])
check("a date without a year: this year, or last year when that's ahead",
      round((now - ap.when("Nov 6", now)) / day, 1) == 1.5 and int((now - ap.when("Dec 25", now)) / day) == 317,
      (ap.when("Nov 6", now), (now - ap.when("Dec 25", now)) / day))
check("what can't be read is None", ap.when("soon", now) is None and ap.when("", now) is None)
fresh = [{"date": d} for d in ("2019-01-01", "2019-06-01")]
check("a series with a chapter in the last 45 days is still coming out, one without isn't, one with no dates is",
      not ap.fresh(fresh, 45, now) and ap.fresh([{"date": "2026-10-01"}], 45, now) and ap.fresh([{"date": ""}], 45, now)
      and ap.fresh(fresh, 0, now))
s = {"days": [4], "time": "06:00"}
fri_noon = datetime(2026, 11, 6, 12, 0).timestamp()
check("the schedule's times", ap.last_due(s, fri_noon) == datetime(2026, 11, 6, 6, 0).timestamp()
      and ap.next_due(s, fri_noon) == datetime(2026, 11, 13, 6, 0).timestamp())

# ---- settings ----
st = app.get(f"{API}/status").json()
check("both sources' lists offered as charts, the NSFW one marked", {c["name"]: (c["nsfw"], [x["id"] for x in c["lists"]]) for c in st["charts"]}
      == {"Made-up site": (False, ["popular", "latest"]), "Spicy site": (True, ["popular", "latest"])}, st["charts"])
check("nothing set up: off, no chart, the defaults", st["settings"]["on"] is False and st["settings"]["charts"] == []
      and st["settings"]["top"] == 5 and st["next"] is None)
r = app.put(f"{API}/settings", json={"settings": {"time": "25:00"}})
check("a time that isn't one refused", r.status_code == 400, r.text)
r = app.put(f"{API}/settings", json={"settings": {"on": True}}, headers={"Origin": "https://example.com"})
check("a change from another web page refused", r.status_code == 403, r.text)
r = app.post(f"{API}/run", json={"dry": True})
check("a run with no chart refused", r.status_code == 400, r.text)
r = app.put(f"{API}/settings", json={"settings": {
    "charts": [{"key": "plugin:madeup", "list": "popular"}, {"key": "plugin:spicy", "list": "popular"}], "top": 3, "first": 1,
    "each": 2, "budget": 500, "fresh_days": 45, "skip": ["skip me", " "], "days": [0, 1, 2, 3, 4, 5, 6, 9], "time": "03:30",
    "recap": "new", "youtube": True, "privacy": "unlisted"}})
check("saved, within limits", r.ok and r.json()["settings"]["budget"] == 100 and r.json()["settings"]["skip"] == ["skip me"]
      and r.json()["settings"]["days"] == list(range(7)) and len(r.json()["settings"]["charts"]) == 2, r.text)

# ---- a dry run: the sieve ----
ap.youtube_state = lambda: {"installed": True, "connected": True}  # (Post to YouTube stood in for)
ap.start_run("dry run", dry=True)
st = ap._state()
lineup = st["lineup"]
check("the plan: the first 3 that pass the sieve, in the chart's order", [(e["title"], e["new"], [c["name"] for c in e["chapters"]]) for e in lineup["entries"]]
      == [("Alpha", True, ["Chapter 2"]), ("Beta", True, ["Chapter 3"]), ("Gamma", True, ["Chapter 2"])], lineup["entries"])
notes = " | ".join(lineup["notes"])
check("passed over, with why: the NSFW source (not allowed), the stalled series, the skip list",
      "Spicy site: an NSFW source" in notes and "Old: nothing new in 45 days" in notes and "Skip Me: on the skip list" in notes, notes)
check("a dry run makes and gets nothing", st.get("run") is None and store.list_all() == [] and "Dry run: 3 series" in st["log"][0]["note"])

# ---- a run ----
ap.start_run("started by you")
check("the run over", run_over(), ap._state().get("run"))
st = ap._state()
alpha, beta, gamma = by_name("Alpha"), by_name("Beta"), by_name("Gamma")
check("projects made, named after the series", all((alpha, beta, gamma)), [p["name"] for p in store.list_all()])
check("with the kind of comic from the source", store.load(alpha)["settings"]["layout"] == "webtoon"
      and store.load(beta)["settings"]["layout"] == "pages" and store.load(beta)["settings"]["reading_direction"] == "rtl"
      and store.load(gamma)["settings"]["layout"] == "auto")
check("and nothing to stop and ask", all(store.load(p)["settings"][k] == "trust" for p in (alpha, beta, gamma)
                                         for k in ("credits_review", "characters_review")))
check("the newest chapter got, as Get chapters gets it, its pages too", names(alpha) == ["Chapter 2"] and names(beta) == ["Chapter 3"]
      and len(store.load(alpha)["pages"]) == 2, (names(alpha), names(beta)))
check("then Make the recap for just it", [sorted(set(j["steps"])) for j in jobs(alpha)] == [["download"], ["test.fake"]]
      and jobs(alpha)[-1]["status"] == "done" and jobs(alpha)[-1]["chapters"] == [store.load(alpha)["chapters"][0]["id"]],
      [(j["steps"], j["status"], j["chapters"]) for j in jobs(alpha)])
yt = json.loads((app.data / "plugin-data" / "youtube-post" / "projects.json").read_text(encoding="utf-8"))
check("Post to YouTube told to post each one's video, as unlisted", all(yt[p] == {"when": "auto", "privacy": "unlisted"} for p in (alpha, beta, gamma)), yt)
digest = st["runs"][0]
check("the run's digest", digest["summary"] == "3 series, 3 chapters got, 3 recaps made" and digest["why"] == "started by you"
      and [e["status"] for e in digest["entries"]] == ["done"] * 3, digest)
check("the series remembered", {v["title"] for v in st["series"].values()} == {"Alpha", "Beta", "Gamma"})

# ---- seen before: only what's new; a project made by hand found; the budget ----
world["series"]["alpha"]["chapters"] += [[3, "today"], [4, "today"], [5, "today"]]
world["series"]["gamma"]["chapters"] += [[3, ""]]
world["chart"] = ["alpha", "beta", "gamma", "delta"]
world["series"]["delta"] = {"kind": "webtoon", "chapters": [[1, "today"], [2, "today"]]}
WORLD.write_text(json.dumps(world), encoding="utf-8")
delta = app.project("Delta by hand")
store.update(delta, lambda p: p.update(source={"key": "plugin:madeup", "ref": "delta", "url": "", "title": "Delta", "skipped": []}))
store.update(delta, lambda p: p["chapters"].append({"id": "c0001", "name": "Chapter 1", "source_ref": "delta/1"}))
ap.start_run("dry run", dry=True)
lineup = ap._state()["lineup"]
check("a series seen before: its new chapters, at most 2 of the 3, the newest; one with nothing new passed over, not counted; "
      "a project made by hand from the same source found and given its new chapter",
      [(e["title"], e["new"], e["pid"] == delta, [c["name"] for c in e["chapters"]]) for e in lineup["entries"]]
      == [("Alpha", False, False, ["Chapter 4", "Chapter 5"]), ("Gamma", False, False, ["Chapter 3"]), ("Delta", False, True, ["Chapter 2"])]
      and "Beta: no new chapters" in " | ".join(lineup["notes"]), (lineup["entries"], lineup["notes"]))
app.put(f"{API}/settings", json={"settings": {"budget": 3}})
ap.start_run("dry run", dry=True)
lineup = ap._state()["lineup"]
check("the budget cuts the run", [(e["title"], [c["name"] for c in e["chapters"]]) for e in lineup["entries"]]
      == [("Alpha", ["Chapter 4", "Chapter 5"]), ("Gamma", ["Chapter 3"])], lineup["entries"])
app.put(f"{API}/settings", json={"settings": {"budget": 100}})

# ---- a recap that stops to ask ends the run; its chapters are first next time ----
BEHAVIOUR["Alpha"] = "ask"
ap.start_run("scheduled")
check("the run over early", run_over())
st = ap._state()
digest = st["runs"][0]
check("Alpha stopped to ask: cancelled where it waited, the run ended, the rest not started",
      [e["status"] for e in digest["entries"]] == ["left", "skipped", "skipped"] and "stopped to ask" in digest["grounded"]
      and names(alpha) == ["Chapter 2", "Chapter 4", "Chapter 5"], digest)
check("its recap left for next time", [x["pid"] for x in st["leftover"]] == [alpha] and len(st["leftover"][0]["ids"]) == 2)
left_ids = st["leftover"][0]["ids"]
check("nothing it did lost: the chapters are there", jobs(alpha)[-1]["status"] == "cancelled" and len(store.load(alpha)["chapters"]) == 3)
BEHAVIOUR.pop("Alpha")
ap.start_run("scheduled")
check("the next run over", run_over())
st = ap._state()
digest = st["runs"][0]
check("its recap first, for the chapters got then; the others theirs", [e["title"] for e in digest["entries"]] == ["Alpha", "Gamma", "Delta"]
      and all(e["status"] == "done" for e in digest["entries"]) and jobs(alpha)[-1]["status"] == "done"
      and jobs(alpha)[-1]["chapters"] == left_ids, (digest["entries"], jobs(alpha)[-1]["chapters"], left_ids))
check("the leftover gone, nothing got twice", st["leftover"] == [] and names(alpha) == ["Chapter 2", "Chapter 4", "Chapter 5"]
      and names(delta) == ["Chapter 1", "Chapter 2"], (st["leftover"], names(alpha), names(delta)))

# ---- Stop, and the routes ----
world["series"]["beta"]["chapters"] += [[4, "today"]]
world["series"]["gamma"]["chapters"] += [[4, ""]]
WORLD.write_text(json.dumps(world), encoding="utf-8")
BEHAVIOUR["Beta"] = "slow"
r = app.post(f"{API}/run", json={"dry": False})
check("Run now from the page", r.ok and (r.json()["run"] or r.json()["runs"]), r.text)
check("a second run refused meanwhile", until(lambda: ap._state().get("run") and ap._state()["run"]["stage"] == "flying", 30)
      and app.post(f"{API}/run", json={}).status_code == 409)
until(lambda: any(j["status"] == "running" and "test.fake" in j["steps"] for j in jobs(beta)), 40)
r = app.post(f"{API}/stop", json={})
check("Stop: the job going on finishes, no other series starts", r.ok and run_over() and
      [e["status"] for e in ap._state()["runs"][0]["entries"]] == ["done", "skipped"], ap._state()["runs"][0])
check("stopped from another web page refused", app.post(f"{API}/stop", json={}, headers={"Origin": "https://evil.example"}).status_code == 403)
BEHAVIOUR.pop("Beta")

# ---- the schedule ----
app.put(f"{API}/settings", json={"settings": {"on": True, "days": list(range(7)), "time": "00:00"}})
with ap._lock:
    st = ap._state()
    st["last"] = time.time() - 2 * day  # the app was closed at the last scheduled time
    ap._write("state.json", st)
n = len(ap._state()["runs"])
ap.tick()
check("a time missed while the app was closed: run when it next looks", until(lambda: len(ap._state()["runs"]) > n, 60)
      and ap._state()["runs"][0]["why"] == "scheduled", ap._state()["runs"][0] if ap._state()["runs"] else None)
ap.tick()
time.sleep(0.5)
check("not again until the next time", len(ap._state()["runs"]) == n + 1 and ap._state().get("run") is None)
st = app.get(f"{API}/status").json()
check("the page's status: on, the next time, the last run, the log", st["settings"]["on"] and st["next"] and st["last"]
      and st["log"] and st["runs"][0]["summary"], {k: st[k] for k in ("next", "last")})
done()
