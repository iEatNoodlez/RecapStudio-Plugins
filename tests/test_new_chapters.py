"""New chapters on a schedule, against a made-up source on this PC (a source plugin whose series' chapters the test
adds to, with pictures served on 127.0.0.1) and a stand-in for Make the recap's steps: the schedule's times, each
project's choice (not checked, counted, got, got and recapped alone, got and recapped whole), a project with a job
running getting its chapters after it, a source that's down, a check when the app was closed at the time, and nothing
got twice."""
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime

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
app = App(plugins=["new-chapters"])
app.fake_recap(BEHAVIOUR)
pipeline, plugins, store = app.pipeline, app.plugins, app.store
from recap import sources  # noqa: E402

SERIES = app.data / "made-up-series.json"  # {series ref: [chapter numbers]}, read by the source each time
SERIES.write_text(json.dumps({"moon": [1, 2], "river": [1], "sun": [1, 2, 3], "busy": [1]}), encoding="utf-8")
(app.data / "plugins" / "madeup.py").write_text(f'''
import json
RECAP_PLUGIN = 1
NAME = "Made-up site"
SITES = ["127.0.0.1"]

def series(url):
    numbers = json.loads(open({str(SERIES)!r}, encoding="utf-8").read())[url]
    return {{"title": url.title(), "chapters": [{{"url": f"{{url}}/{{n}}", "name": f"Chapter {{n}}", "number": n}}
                                             for n in numbers]}}

def pages(chapter):
    return [f"{BASE}/{{chapter}}/{{i}}.png" for i in range(2)]
''', encoding="utf-8")

check("plugin loaded", app.loaded("new-chapters"), plugins.loaded.get("new-chapters"))
nc = app.module("new-chapters")
KEY = "new-chapters.when_new"
check("its project setting: got by default, offered to New project",
      store.DEFAULT_SETTINGS.get(KEY) == "get" and KEY in store.NEW_PROJECT_KEYS)


def project(name, ref, choice=None, key="plugin:madeup"):
    pid = app.project(name, **({KEY: choice} if choice else {}))
    store.update(pid, lambda p: p.update(source={"key": key, "ref": ref, "url": "", "title": ref.title(), "skipped": []}))
    return pid


def chapters(pid):
    return [c["name"] for c in store.load(pid)["chapters"]]


def jobs(pid):  # (the stand-in step is in a job's steps twice: it's in STEPS and EXTRA_STEPS)
    return sorted((j for j in pipeline.jobs.values() if j["project"] == pid), key=lambda j: j["created"])


# ---- the schedule's times ----
s = {"days": [4], "time": "06:00"}  # Fridays
fri_noon = datetime(2026, 11, 6, 12, 0).timestamp()  # a Friday
check("the last time due: this Friday 06:00", nc.last_due(s, fri_noon) == datetime(2026, 11, 6, 6, 0).timestamp())
check("before it on a Friday: last Friday's", nc.last_due(s, datetime(2026, 11, 6, 5, 0).timestamp())
      == datetime(2026, 10, 30, 6, 0).timestamp())
check("the next: next Friday", nc.next_due(s, fri_noon) == datetime(2026, 11, 13, 6, 0).timestamp())
check("every day at 23:30", nc.next_due({"days": list(range(7)), "time": "23:30"}, fri_noon)
      == datetime(2026, 11, 6, 23, 30).timestamp())
r = app.put("/api/plugins/new-chapters/settings", json={"days": [0, 9], "at": "25:00"})
check("a time that isn't one refused", r.status_code == 400, r.text)
r = app.put("/api/plugins/new-chapters/settings", json={"days": [0, 4, 9], "at": "06:30"})
check("saved (days that aren't days dropped)", r.ok and r.json()["settings"] == {"days": [0, 4], "time": "06:30"}, r.text)

# ---- one check ----
moon = project("Moon", "moon")                     # got (the default)
river = project("River", "river", "tell")          # counted only
sun = project("Sun", "sun", "recap")               # got, then recapped alone
off = project("Off", "moon", "off")                # not checked
down = project("Down", "gone", "get", key="plugin:nosuch")  # its source isn't there
# (a new project starts from the last one's choice, as with the kind of comic)
plain = app.project("Plain")                       # not made from a site
store.update(sun, lambda p: (p["chapters"].append({"id": "c0001", "name": "Chapter 1", "source_ref": "sun/1"})))
nc.check_all()
check("got: the new chapters, as Get chapters gets them", until(lambda: chapters(moon) == ["Chapter 1", "Chapter 2"], 30),
      chapters(moon))
check("their pages", len(store.load(moon)["pages"]) == 4, len(store.load(moon)["pages"]))
check("counted only: nothing got, the count shown", chapters(river) == [] and sources.new_counts.get(river) == 1)
check("not checked", chapters(off) == [] and off not in sources.new_counts)
check("recapped alone: got, then Make the recap for just them", until(
    lambda: len(jobs(sun)) == 2 and jobs(sun)[-1]["status"] == "done", 30), [(j["steps"], j["status"]) for j in jobs(sun)])
ids = [c["id"] for c in store.load(sun)["chapters"] if c["name"] in ("Chapter 2", "Chapter 3")]
check("... its chapters: the new ones", jobs(sun)[-1]["chapters"] == ids and set(jobs(sun)[-1]["steps"]) == {"test.fake"},
      jobs(sun)[-1]["chapters"])
check("the one already there not got again", chapters(sun) == ["Chapter 1", "Chapter 2", "Chapter 3"], chapters(sun))
check("a project not from a site left alone", jobs(plain) == [])
log = app.get("/api/plugins/new-chapters/status").json()
notes = {x["project"]: x["note"] for x in reversed(log["log"])}
check("the source that's down said, the rest checked", "couldn't check" in notes.get("Down", ""), notes)
check("what happened, in Settings", "getting them" in json.dumps(log["log"]) and "Got 2 new chapters" in notes.get("Moon", "")
      and "Make the recap started for the new chapters" in notes.get("Sun", ""), log["log"][:6])
check("its projects listed with their choices", {p["name"]: p["choice"] for p in log["projects"]}
      == {"Moon": "get", "River": "tell", "Sun": "recap", "Off": "off", "Down": "get"}, log["projects"])

# ---- a project with a job running, the whole recap, and the schedule ----
SERIES.write_text(json.dumps({"moon": [1, 2, 3], "river": [1], "sun": [1, 2, 3], "busy": [1]}), encoding="utf-8")
busy = project("Busy", "busy", "all")
BEHAVIOUR["Busy"] = "slow"
first = app.plugins.Plugin.make_recap(busy)
until(lambda: pipeline.jobs[first["id"]]["status"] == "running", 10)
with nc._lock:  # the app was closed at the last scheduled time: the next look checks
    st = nc._state()
    st["last"] = time.time() - 8 * 86400
    nc._write("state.json", st)
nc.tick()
check("checked when the app opens after a time it missed", until(lambda: chapters(moon) == ["Chapter 1", "Chapter 2", "Chapter 3"], 30),
      chapters(moon))
check("a project with a job running waits for it", chapters(busy) == [] and nc._state()["pending"][busy]["stage"] == "get")
pipeline.jobs[first["id"]]["cancel"] = True
check("... then gets its chapters, then makes the whole recap", until(
    lambda: [sorted(set(j["steps"])) for j in jobs(busy)] == [["test.fake"], ["download"], ["test.fake"]]
    and jobs(busy)[-1]["status"] in ("running", "done"), 40), [(j["steps"], j["status"]) for j in jobs(busy)])
check("... for every chapter", jobs(busy)[-1]["chapters"] is None)
pipeline.jobs[jobs(busy)[-1]["id"]]["cancel"] = True
n = len(jobs(moon))
nc.tick()
check("not checked again until the next time", len(jobs(moon)) == n and nc._state()["last"] > time.time() - 60)
nc.check_all()
check("nothing got twice", chapters(moon) == ["Chapter 1", "Chapter 2", "Chapter 3"] and len(jobs(moon)) == n)
check("Check now", app.post("/api/plugins/new-chapters/check").status_code == 200)
done()
