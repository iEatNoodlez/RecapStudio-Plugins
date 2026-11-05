"""When a video is done, with a made-up finished video, a stand-in for ntfy and a webhook on this PC, and a stand-in
for Make the recap's steps: the copy (a folder per project, subtitles, thumbnail, Shorts), the command (its places
filled in and quoted, and its variables), telling the phone (a video done, a recap that stops to ask, told once, and
one that fails), a project left out, the test button, and the command refused from a web page."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import App, check, done, until

heard = []  # (path, body) the stand-in got


class Phone(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        heard.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


phone = ThreadingHTTPServer(("127.0.0.1", 0), Phone)
threading.Thread(target=phone.serve_forever, daemon=True).start()
PHONE = f"http://127.0.0.1:{phone.server_address[1]}"

BEHAVIOUR = {}
app = App(plugins=["when-done"])
app.fake_recap(BEHAVIOUR)
pipeline, plugins, store = app.pipeline, app.plugins, app.store
check("plugin loaded", app.loaded("when-done"), plugins.loaded.get("when-done"))
wd = app.module("when-done")
check("its page script served", app.get("/plugin-web/when-done/plugin.js").status_code == 200)

# a project with a finished video in two parts, subtitles, a thumbnail and a Short
pid = app.project("Moon's Tower")
d = store.project_dir(pid)
(d / "output" / "shorts").mkdir(parents=True)
for name, data in (("recap - Part 1.mp4", b"video one"), ("recap - Part 2.mp4", b"video two"), ("recap - Part 1.srt", b"1"),
                   ("recap - Part 2.srt", b"2"), ("thumbnail.jpg", b"jpg"), ("shorts/recap - Short 1.mp4", b"short")):
    (d / "output" / name).write_bytes(data)
store.update(pid, lambda p: p.update(output={
    "video": "output/recap - Part 1.mp4", "srt": "output/recap - Part 1.srt", "time": 1, "shorts": ["output/shorts/recap - Short 1.mp4"],
    "parts": [{"video": "output/recap - Part 1.mp4", "srt": "output/recap - Part 1.srt", "seconds": 1500},
              {"video": "output/recap - Part 2.mp4", "srt": "output/recap - Part 2.srt", "seconds": 1260}]}))

OUT = app.data / "Finished recaps"
SAID = app.data / "said.json"
SCRIPT = app.data / "say.py"
SCRIPT.write_text("import json, os, sys\n"
                  f"json.dump({{'args': sys.argv[1:], 'project': os.environ['RECAP_PROJECT'], 'cwd': os.getcwd()}}, "
                  f"open({str(SAID)!r}, 'w', encoding='utf-8'))\nprint('sent it')\n", encoding="utf-8")
r = app.put("/api/plugins/when-done/settings", json={"changes": {"command": "calc"}}, headers={"Origin": "https://example.com"})
check("the command refused from a web page", r.status_code == 403, r.text)
check("a folder that isn't a full path refused",
      app.put("/api/plugins/when-done/settings", json={"changes": {"folder": "recaps"}}).status_code == 400)
check("a topic that isn't one refused",
      app.put("/api/plugins/when-done/settings", json={"changes": {"ntfy_topic": "a b/c"}}).status_code == 400)
r = app.put("/api/plugins/when-done/settings", json={"changes": {
    "folder": str(OUT), "shorts": True, "command": f'"{sys.executable}" "{SCRIPT}" {{video}} "{{project}}" {{videos}} {{copy}}',
    "ntfy_topic": "recap-studio-test", "ntfy_server": PHONE, "webhook": f"{PHONE}/hook", "not-a-setting": 1}})
check("saved", r.ok and r.json()["settings"]["ntfy_topic"] == "recap-studio-test" and "not-a-setting" not in r.json()["settings"], r.text)

# ---- a video done ----
plugins.emit("job_done", job={"id": "j1", "project": pid, "status": "done", "steps": ["video"]})
check("done on its own thread, then logged", until(lambda: any("command" in x["note"] for x in wd._read("log.json", [])), 30),
      wd._read("log.json", []))
mine = OUT / "Moon's Tower"
check("copied into a folder named after the project: the parts, subtitles and thumbnail",
      sorted(x.name for x in mine.iterdir()) == ["Shorts", "recap - Part 1 - thumbnail.jpg", "recap - Part 1.mp4",
                                                 "recap - Part 1.srt", "recap - Part 2.mp4", "recap - Part 2.srt"],
      sorted(x.name for x in mine.iterdir()) if mine.exists() else None)
check("... and its Shorts", (mine / "Shorts" / "recap - Short 1.mp4").read_bytes() == b"short")
check("... nothing left half-named", not list(OUT.rglob("*.copying")))
said = json.loads(SAID.read_text(encoding="utf-8")) if SAID.exists() else {}
check("the command ran in the output folder, its places filled in and quoted once", said.get("args") == [
    str(d / "output" / "recap - Part 1.mp4"), "Moon's Tower", str(d / "output" / "recap - Part 1.mp4"),
    str(d / "output" / "recap - Part 2.mp4"), str(mine / "recap - Part 1.mp4")] and said.get("cwd") == str(d / "output"), said)
check("... with its variables", said.get("project") == "Moon's Tower")
if os.name == "nt":
    check("a title can't break out of its quotes", wd.command_line("echo {title}", {"title": 'a" & calc & "b'})
          == 'echo "a & calc & b"', wd.command_line("echo {title}", {"title": 'a" & calc & "b'}))
else:
    line = wd.command_line("echo {title} '{project}'", {"title": "$(touch pwned) `id`", "project": "Moon's"})
    check("a title can't run anything", line == "echo '$(touch pwned) `id`' 'Moon'\"'\"'s'", line)
check("its last line logged", any(x["note"] == "The command ran: sent it" for x in wd._read("log.json", [])))
check("the phone told by ntfy", until(lambda: any(p == "/" and b.get("topic") == "recap-studio-test" for p, b in heard), 10)
      and any("Moon's Tower: its video is done (46 min in 2 parts)." == b.get("message") for p, b in heard if p == "/"), heard)
until(lambda: any(p == "/hook" for p, b in heard), 10)  # (sent after ntfy)
hook = next((b for p, b in heard if p == "/hook"), {})
check("and the webhook, with names and no paths", hook.get("event") == "video_done" and hook.get("files") ==
      ["recap - Part 1.mp4", "recap - Part 2.mp4"] and "\\" not in json.dumps(hook) and str(app.data) not in json.dumps(hook), hook)
sent = []
real_post = wd.requests.post
wd.requests.post = lambda url, json=None, **kw: sent.append((url, json)) or type("R", (), {"raise_for_status": lambda self: None})()
wd.tell({**wd.DEFAULTS, "webhook": "https://discord.com/api/webhooks/1/abc"}, "T", "M", {"project": "P"})
wd.tell({**wd.DEFAULTS, "webhook": "https://hooks.slack.com/services/x"}, "T", "M", {"project": "P"})
wd.requests.post = real_post
check("... Discord: content; Slack: text", sent == [("https://discord.com/api/webhooks/1/abc", {"content": "**T**\nM"}),
                                                     ("https://hooks.slack.com/services/x", {"text": "*T*\nM"})], sent)

# ---- a recap that stops to ask, and one that fails ----
heard.clear()
BEHAVIOUR.update({"Asks": "ask", "Fails": "fail"})
asks, fails = app.project("Asks"), app.project("Fails")
job = plugins.Plugin.make_recap(asks)
until(lambda: (pipeline.latest_for(asks) or {}).get("pause_reason"), 10)
wd.look()
wd.look()
check("a recap that stops to ask: told once", [b.get("message") for p, b in heard if p == "/"] ==
      ["Asks: Paused before the script: the character scan found 3 characters to check"], heard)
pipeline.jobs[job["id"]]["cancel"] = True
heard.clear()
plugins.Plugin.make_recap(fails)
check("a recap that fails: told", until(lambda: any("Fails:" in b.get("message", "") and "failed" in b.get("message", "")
                                                     for p, b in heard if p == "/"), 15), heard)

# ---- a project left out, and the buttons ----
heard.clear()
app.put(f"/api/projects/{pid}/settings", json={"when-done.use": "no"})
wd.after_job({"id": "j2", "project": pid, "status": "done", "steps": ["video"]})
check("a project left out: nothing done", not until(lambda: heard, 3))
r = app.post(f"/api/plugins/when-done/now/{pid}")
check("Do it now, from the Video tab (even for a project left out)", r.ok and until(lambda: heard, 15), r.text)
check("Do it now for a project with no video: says so", app.post(f"/api/plugins/when-done/now/{asks}").status_code == 400)
heard.clear()
r = app.post("/api/plugins/when-done/test")
check("Send a test", r.ok and any("test" in b.get("message", "") for p, b in heard), (r.text, heard))
done()
