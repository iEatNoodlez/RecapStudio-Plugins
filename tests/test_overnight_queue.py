"""The Overnight queue, with a stand-in for Make the recap's steps (done, failed, stopped to ask, slow): leaving a project
for the morning, waiting for you, the stop time, a start time, refusing a second queue, and carrying on after a restart."""
import json
import time

import requests

from helpers import App, check, done, until

BEHAVIOUR = {}
app = App(plugins=["overnight-queue"])
app.fake_recap(BEHAVIOUR)
pipeline, plugins, store = app.pipeline, app.plugins, app.store


class _Client:
    def __getattr__(self, method):
        return lambda path, **kw: requests.request(method.upper(), app.base + path, timeout=30, **kw)


client = _Client()


def project(name):
    return app.project(name)


check("plugin loaded", app.loaded("overnight-queue"), plugins.loaded.get("overnight-queue"))
oq = app.module("overnight-queue")

A, B, C, D = (project(n) for n in ("Queue A", "Queue B", "Queue C", "Queue D"))
BEHAVIOUR.update({"Queue B": "ask", "Queue C": "fail"})
qfile = oq._plugin.data_dir / "queue.json"


def state():
    return oq._state()


def statuses():
    return [(it["name"], it["status"]) for it in state()["items"]]


# ---- 1. leave for the morning: B stops to ask and is left, C fails, the rest are done ----
res = client.post("/api/plugins/overnight-queue/start", json={"projects": [A, B, C, D]})
check("queue starts", res.status_code == 200 and res.json()["state"] in ("waiting", "running"), res.text)
ok = until(lambda: state()["state"] == "done", 30)
check("queue finished", ok, statuses())
check("each project's result", statuses() == [("Queue A", "done"), ("Queue B", "left"), ("Queue C", "failed"), ("Queue D", "done")],
      statuses())
items = state()["items"]
check("left one says what it asked", "character scan found 3" in items[1]["note"], items[1]["note"])
check("failed one says why", "isn't set up" in items[2]["note"], items[2]["note"])
check("B's job was cancelled where it waited", pipeline.latest_for(B)["status"] == "cancelled")

# ---- 2. wait for me: B waits until resumed ----
client.post("/api/plugins/overnight-queue/clear")
res = client.post("/api/plugins/overnight-queue/start", json={"projects": [B, A], "on_ask": "wait"})
check("waits on a question", until(lambda: pipeline.latest_for(B)["status"] == "paused", 10)
      and (time.sleep(1.5) or statuses()[0][1] == "running") and statuses()[1][1] == "waiting", statuses())
BEHAVIOUR["Queue B"] = "done"
pipeline.latest_for(B)["pause"] = False  # Resume
check("carries on once answered", until(lambda: state()["state"] == "done", 20) and
      statuses() == [("Queue B", "done"), ("Queue A", "done")], statuses())

# ---- 3. the stop time pauses the running project, and nothing else starts ----
BEHAVIOUR.update({"Queue A": "slow"})
client.post("/api/plugins/overnight-queue/clear")
client.post("/api/plugins/overnight-queue/start", json={"projects": [A, D], "stop_at": "06:00"})
until(lambda: pipeline.latest_for(A)["status"] == "running", 10)
with oq._lock:  # (as the plugin does: its timer could be saving the queue right now)
    q = json.loads(qfile.read_text())
    q["stop"] = time.time() + 1  # (as if 06:00 came)
    qfile.write_text(json.dumps(q))
check("stopped at the stop time", until(lambda: state()["state"] == "stopped", 10), state()["state"])
check("running project paused, next not started", until(lambda: pipeline.latest_for(A)["status"] == "paused", 5)
      and statuses() == [("Queue A", "paused"), ("Queue D", "waiting")], statuses())
pipeline.latest_for(A)["cancel"] = True
until(lambda: pipeline.latest_for(A)["status"] == "cancelled", 10)

# ---- 4. a start time in the future waits; stop before it ----
client.post("/api/plugins/overnight-queue/clear")
later = time.strftime("%H:%M", time.localtime(time.time() + 3600))
res = client.post("/api/plugins/overnight-queue/start", json={"projects": [D], "start_at": later})
time.sleep(1)
check("waits for its start time", state()["state"] == "waiting" and statuses() == [("Queue D", "waiting")]
      and state()["start_clock"] == later, (state()["state"], statuses()))
check("can't start a second queue", client.post("/api/plugins/overnight-queue/start", json={"projects": [D]}).status_code == 409)
client.post("/api/plugins/overnight-queue/stop")
check("stopped by hand", state()["state"] == "stopped")
check("bad time refused", client.post("/api/plugins/overnight-queue/start", json={"projects": [D], "start_at": "25:00"}).status_code == 400)
check("_at rolls over midnight", oq._at("00:30", time.mktime((2026, 11, 1, 23, 0, 0, 0, 0, -1)))
      == time.mktime((2026, 11, 2, 0, 30, 0, 0, 0, -1)))

# ---- 5. after a restart the running project starts again (its job is gone: the new app never ran it) ----
client.post("/api/plugins/overnight-queue/clear")
before = pipeline.latest_for(D)["id"]
with oq._lock:
    qfile.write_text(json.dumps({"state": "running", "start": time.time(), "stop": None, "on_ask": "skip",
                                 "items": [{"project": D, "status": "running", "job": "gone-with-the-old-app", "note": ""}]}))
oq.tick()
check("re-runs the project after a restart", until(lambda: state()["state"] == "done", 15)
      and pipeline.latest_for(D)["id"] != before and statuses() == [("Queue D", "done")], statuses())

done()
