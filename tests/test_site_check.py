"""Site check, against the app's made-up comic site (App/tests/fakesite.py) behind its stand-in for Cloudflare's check
for a person, with a stand-in for the window (fake_window.py, in place of pywebview): the site asking and the plugin
being told; the window opened only when asked, passing, closed first and left open; the pass used for search, a
series and getting chapters into a project; kept across a restart, and not once it's run out; a site that refuses
a fresh pass; forgetting; one window at a time; Recap Studio opened in a browser. No real site is visited."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from helpers import APP, App, check, done, free_port, until

HERE = Path(__file__).parent
os.environ["RECAP_SITECHECK_WINDOW"] = str(HERE / "fake_window.py")
SITE = free_port()
SITE_URL = f"http://127.0.0.1:{SITE}"
fake = subprocess.Popen([sys.executable, str(APP / "tests" / "fakesite.py"), str(SITE), str(HERE)])


def example(data: Path):
    text = (APP / "docs" / "plugin-example.py").read_text(encoding="utf-8")
    text = text.replace('BASE = "https://example.com"', f'BASE = "{SITE_URL}"')
    text = text.replace('SITES = ["example.com"]', 'SITES = ["127.0.0.1"]')
    text = text.replace('HEADERS = {"Referer": "https://example.com/"}', f'HEADERS = {{"Referer": "{SITE_URL}/"}}')
    (data / "plugins").mkdir(parents=True, exist_ok=True)
    (data / "plugins" / "example.py").write_text(text, encoding="utf-8")


app = App(plugins=["site-check"], prepare=example)
from recap import sources  # noqa: E402

sc = app.module("site-check")
import requests  # noqa: E402

until(lambda: requests.get(SITE_URL + "/series/red-river", timeout=2).ok, 20)


def guard(**q):
    requests.get(SITE_URL + "/admin/guard", params={"on": "1", **q}, timeout=5)


def search():
    return app.post("/api/sources/search", json={"key": "plugin:example", "query": "moon", "page": 1})


def status():
    return app.get("/api/plugins/site-check/status").json()


def wait_window():
    until(lambda: status()["window"]["state"] != "open", 20)
    return status()["window"]


window = sys.modules["recap_sitecheck_window"] if "recap_sitecheck_window" in sys.modules else None
try:
    check("plugin loaded", app.loaded("site-check"), app.plugins.loaded.get("site-check"))
    st = status()
    check("nothing yet: no passes, no sites waiting, the window can open", st["passes"] == [] and st["waiting"] == []
          and st["can_open"] and st["window"]["state"] == "idle", st)

    print("-- a site that asks")
    guard(reset="1")
    r = search()
    told = json.loads(r.headers.get("X-Site-Check") or "null") or {}
    check("the search is refused with the site's check", r.status_code == 400 and "wants to check you're a person"
          in r.json()["detail"], r.text)
    check("with the plugin there, the message doesn't send you to it", "Site check plugin" not in r.json()["detail"])
    check("the page is told the site and the source", told.get("host") == "127.0.0.1" and told.get("source") ==
          "Example Comics", told)
    st = status()
    check("the site is listed as asking", [w["host"] for w in st["waiting"]] == ["127.0.0.1"]
          and st["waiting"][0]["source"] == "Example Comics" and not st["waiting"][0]["refused"], st["waiting"])
    window = sys.modules.get("recap_sitecheck_window")
    check("no window opened by itself", window is None or window.opened == [])

    print("-- the window: closed first, left open, passed")
    os.environ["FAKE_WINDOW"] = "close"
    r = app.post("/api/plugins/site-check/open", json={"url": told["url"]})
    window = sys.modules["recap_sitecheck_window"]
    check("Open the site opens a window on the page that asked", r.ok and window.opened[-1][1] == told["url"]
          and "127.0.0.1" in window.opened[-1][0], (r.text, window.opened))
    w = wait_window()
    check("closed before passing: said so, and no pass", w["state"] == "closed" and "closed" in w["message"]
          and status()["passes"] == [], w)
    os.environ["FAKE_WINDOW"] = "stay"
    sc.WINDOW_TIMEOUT = 4
    app.post("/api/plugins/site-check/open", json={"url": told["url"]})
    r = app.post("/api/plugins/site-check/open", json={"url": told["url"]})
    check("one window at a time", r.status_code == 409 and "open already" in r.json()["detail"], r.text)
    w = wait_window()
    check("one left on the check gives up after a while, and closes", w["state"] == "timeout"
          and window.windows == [], w)
    sc.WINDOW_TIMEOUT = 600
    os.environ["FAKE_WINDOW"] = "pass"
    app.post("/api/plugins/site-check/open", json={"url": told["url"]})
    w = wait_window()
    st = status()
    check("passed: the window closes and says so", w["state"] == "passed" and "passed" in w["message"]
          and window.windows == [], w)
    check("the pass is listed, for the site, with when it runs out (the cookie's: 30 minutes)",
          [p["domain"] for p in st["passes"]] == ["127.0.0.1"] and st["passes"][0]["clearance"]
          and 1700 < st["passes"][0]["expires"] - time.time() < 1810, st["passes"])
    check("and the site isn't waiting any more", st["waiting"] == [], st["waiting"])
    check("its cookies aren't shown to the page", "cookies" not in json.dumps(st))
    check("the app has it, with the window's browser name", sources.site_passes().get("127.0.0.1", {})
          .get("user_agent") == window.AGENT, sources.site_passes())

    print("-- the pass at work")
    r = search()
    check("search gets through", r.ok and [x["title"] for x in r.json()["items"]] == ["The Moon Tower", "Moonlight Cafe"],
          r.text)
    s = app.post("/api/sources/series", json={"key": "plugin:example", "ref": f"{SITE_URL}/series/red-river"}).json()
    check("so does a series", s.get("title") == "Red River Days" and len(s.get("chapters", [])) == 2, s)
    pid = app.project("Red River Days")
    r = app.post(f"/api/projects/{pid}/download", json={"key": "plugin:example", "series": s, "chapters": s["chapters"]})
    check("getting its chapters starts", r.ok, r.text)
    until(lambda: (app.pipeline.latest_for(pid) or {}).get("status") in ("done", "error"), 60)
    job = app.pipeline.latest_for(pid)
    p = app.store.load(pid)
    check("and the chapters' pictures come through the pass", job["status"] == "done" and len(p["chapters"]) == 2
          and len(p["pages"]) == 6, (job.get("error"), len(p["pages"])))
    kept = json.loads((app.data / "plugin-data" / "site-check" / "passes.json").read_text(encoding="utf-8"))
    check("kept in its data folder", list(kept) == ["127.0.0.1"] and kept["127.0.0.1"]["user_agent"] == window.AGENT)

    print("-- a restart, and a pass that's run out")
    sources.forget_pass("127.0.0.1")
    sc._restore()
    check("after a restart, the kept pass is given to the app again", "127.0.0.1" in sources.site_passes())
    check("and works", search().ok)
    kept["127.0.0.1"]["expires"] = time.time() - 5
    (app.data / "plugin-data" / "site-check" / "passes.json").write_text(json.dumps(kept), encoding="utf-8")
    sources.forget_pass("127.0.0.1")
    sc._restore()
    check("one that ran out isn't", "127.0.0.1" not in sources.site_passes() and status()["passes"] == [])
    r = search()
    check("so the site asks again", r.status_code == 400 and "wants to check" in r.json()["detail"], r.text)

    print("-- a site that refuses a fresh pass")
    app.post("/api/plugins/site-check/open", json={"url": told["url"]})
    check("passed again", wait_window()["state"] == "passed")
    guard(strict="1")
    r = search()
    check("the site refuses it: said so", r.status_code == 400 and "didn't take the pass" in r.json()["detail"], r.text)
    w = next((x for x in status()["waiting"] if x["host"] == "127.0.0.1"), {})
    check("and it's marked as one a window can't help with (the pass was fresh)", w.get("refused") and w.get("stubborn"), w)
    guard()

    print("-- forgetting, and the list")
    r = app.delete("/api/plugins/site-check/passes/127.0.0.1")
    check("Forget takes the pass from the app and the file", r.ok and r.json()["passes"] == []
          and sources.site_passes() == {} and json.loads((app.data / "plugin-data" / "site-check" / "passes.json")
                                                         .read_text(encoding="utf-8")) == {}, r.text)
    r = app.delete("/api/plugins/site-check/waiting/127.0.0.1")
    check("a site can be taken off the list", r.ok and r.json()["waiting"] == [], r.text)
    r = app.post("/api/plugins/site-check/open", json={"url": "ftp://example.com/"})
    check("only a site's address opens", r.status_code == 400, r.text)

    print("-- Recap Studio in a browser")
    os.environ.pop("RECAP_SITECHECK_WINDOW")
    st = status()
    check("no window to open: said why", not st["can_open"] and "own window" in st["why"], st)
    r = app.post("/api/plugins/site-check/open", json={"url": told["url"]})
    check("and Open the site says so", r.status_code == 409 and "own window" in r.json()["detail"], r.text)
finally:
    fake.kill()
done()
