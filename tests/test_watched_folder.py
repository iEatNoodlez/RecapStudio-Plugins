"""The Watched folder, with a stand-in for Make the recap's steps: settling and half-copied files, nothing added twice
or moved, unknown folders ignored or made into a project, and the projects folder refused."""
import io
import time
import zipfile

import requests

from helpers import App, check, done, until

BEHAVIOUR = {}
app = App(plugins=["watched-folder"])
app.fake_recap(BEHAVIOUR)
pipeline, plugins, store = app.pipeline, app.plugins, app.store


class _Client:
    def __getattr__(self, method):
        return lambda path, **kw: requests.request(method.upper(), app.base + path, timeout=30, **kw)


client = _Client()


def project(name):
    return app.project(name)


import cv2  # noqa: E402
import numpy as np  # noqa: E402

check("plugin loaded", app.loaded("watched-folder"), plugins.loaded.get("watched-folder"))
wf = app.module("watched-folder")
WATCHED = app.data / "watched"
D = project("Queue D")

# ---- Watched folder ----
def pictures(folder, n):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        cv2.imwrite(str(folder / f"{i + 1:03d}.png"), np.full((300, 200, 3), 40 * i, np.uint8))


def cbz(path, n):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for i in range(n):
            z.writestr(f"{i + 1:03d}.png", cv2.imencode(".png", np.full((300, 200, 3), 90, np.uint8))[1].tobytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b.getvalue())
    return b.getvalue()


WATCHED.mkdir()
res = client.put("/api/plugins/watched-folder/settings", json={"folder": str(store.projects_root()), "recap": True})
check("projects folder refused", res.status_code == 400, res.text)
check("relative path refused", client.put("/api/plugins/watched-folder/settings", json={"folder": "auto_watched"}).status_code == 400)
res = client.put("/api/plugins/watched-folder/settings", json={"folder": str(WATCHED), "recap": True, "create": False})
check("folder saved", res.status_code == 200 and res.json()["settings"]["folder"] == str(WATCHED), res.text)
BEHAVIOUR["Queue D"] = "done"
pictures(WATCHED / "Queue D" / "Chapter 1", 3)
data = cbz(WATCHED / "Queue D" / "Chapter 2.cbz", 2)
(WATCHED / "Queue D" / "Chapter 3.cbz").write_bytes(data[: len(data) // 2])  # still being copied
pictures(WATCHED / "Nobody's series" / "Chapter 1", 2)
wf.look()
check("nothing added on first sight", store.load(D)["chapters"] == [])
wf.SETTLE = 0
wf.look()
p = store.load(D)
names = [c["name"] for c in p["chapters"]]
check("settled chapters added", names == ["Chapter 1", "Chapter 2"], names)
check("their pages", len(p["pages"]) == 5 and all(x.get("src") or x.get("file") for x in p["pages"]), len(p["pages"]))
check("half-copied .cbz waits", "Chapter 3" not in names)
check("nothing moved or deleted", (WATCHED / "Queue D" / "Chapter 1" / "001.png").exists() and (WATCHED / "Queue D" / "Chapter 2.cbz").exists())
check("Make the recap started", until(lambda: (pipeline.latest_for(D) or {}).get("status") == "done", 10))
check("no project made for an unknown folder", not any(x["name"] == "Nobody's series" for x in store.list_all()))
(WATCHED / "Queue D" / "Chapter 3.cbz").write_bytes(data)  # finished copying
wf.look()
check("finished .cbz added next time", [c["name"] for c in store.load(D)["chapters"]][-1] == "Chapter 3")
wf.look()
check("nothing added twice", len(store.load(D)["chapters"]) == 3)
# a chapter added while the project's Make the recap waits behind another project's job: that one job does it too
until(lambda: all(j["status"] not in pipeline.ACTIVE for j in pipeline.jobs.values()), 10)  # (Chapter 3's is done)
for name in ("Queue E", "Queue F"):  # (the tests' app has two job workers: both kept busy)
    BEHAVIOUR[name] = "slow"
    pipeline.submit(project(name), list(pipeline.STEPS))
until(lambda: sum(j["status"] == "running" for j in pipeline.jobs.values()) == 2, 5)
since = time.time()
pictures(WATCHED / "Queue D" / "Chapter 4", 2)
wf.look()
check("its Make the recap waits its turn", (pipeline.latest_for(D) or {}).get("status") == "queued",
      ([c["name"] for c in store.load(D)["chapters"]], (pipeline.latest_for(D) or {}).get("status"),
       client.get("/api/plugins/watched-folder/status").json()["log"][:3]))
pictures(WATCHED / "Queue D" / "Chapter 5", 2)
wf.look()
until(lambda: all(j["status"] not in pipeline.ACTIVE for j in pipeline.jobs.values()), 30)
wf.look()
until(lambda: all(j["status"] not in pipeline.ACTIVE for j in pipeline.jobs.values()), 10)
started = [j for j in pipeline.jobs.values() if j["project"] == D and j["created"] >= since]
check("one Make the recap for chapters added while it waited", len(started) == 1 and len(store.load(D)["chapters"]) == 5,
      [(j["status"], j["created"] - since) for j in started])
client.put("/api/plugins/watched-folder/settings", json={"folder": str(WATCHED), "recap": False, "create": True})
wf.look()
made = [x for x in store.list_all() if x["name"] == "Nobody's series"]
check("unknown folder makes a project when switched on", made and made[0]["chapters"] == 1, made)
log = client.get("/api/plugins/watched-folder/status").json()["log"]
check("status says what it did", any("a new project" in x["note"] for x in log) and any("Make the recap started" in x["note"] for x in log),
      [x["note"] for x in log])
done()
