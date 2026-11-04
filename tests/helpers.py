"""What the plugin tests share: the app they run against, a scratch data folder with the plugins under test copied in,
the app's server in the test's own process, a stand-in for Make the recap's steps, and check().

The app is a Recap Studio checkout (or the dev copy): RECAP_APP, else the first of ../App, ../RecapStudio and
../Release beside this repository that has recap/plugins.py. run.py passes RECAP_APP on to each test.
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent  # this repository
FEATURES = ROOT / "features"
SOURCES = ROOT / "sources"


def find_app() -> Path:
    if os.environ.get("RECAP_APP"):
        return Path(os.environ["RECAP_APP"]).resolve()
    for name in ("App", "RecapStudio", "Release"):
        p = ROOT.parent / name
        if (p / "recap" / "plugins.py").is_file():
            return p
    raise SystemExit("Recap Studio not found: give its folder with --app (or RECAP_APP)")


APP = find_app()
fails: list[str] = []
made: list[Path] = []  # the scratch folders made (scratch)


def scratch(prefix: str = "recap-plugin-test-") -> Path:
    """A new folder in the temp folder, removed by done() when every check passed."""
    made.append(Path(tempfile.mkdtemp(prefix=prefix)))
    return made[-1]


def check(what: str, ok, extra="") -> bool:
    note = "" if ok or extra is None or isinstance(extra, str) and not extra else f"  [{extra}]"
    print(("PASS " if ok else "FAIL ") + what + note, flush=True)
    if not ok:
        fails.append(what)
    return bool(ok)


def until(cond, seconds: float = 30) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class App:
    """The app, in this process, on a scratch data folder with some plugins installed."""

    def __init__(self, plugins=(), sources=(), settings=None, env=None, prepare=None):
        """prepare(data folder): called before the app starts, to put files there (a download it made before)."""
        self.data = scratch()
        for pid in plugins:
            shutil.copytree(FEATURES / pid, self.data / "plugins" / pid, ignore=shutil.ignore_patterns("__pycache__"))
        for sid in sources:
            (self.data / "plugins").mkdir(exist_ok=True)
            shutil.copy(SOURCES / f"{sid}.py", self.data / "plugins" / f"{sid}.py")
        (self.data / "settings.json").write_text(json.dumps({
            "setup_done": True, "check_updates": False, "projects_dir": str(self.data / "Projects"), **(settings or {})}),
            encoding="utf-8")
        if prepare:
            prepare(self.data)
        os.environ.update(RECAP_DATA=str(self.data), **(env or {}))
        sys.path.insert(0, str(APP))
        import uvicorn
        from recap import pipeline, plugins, server, store
        self.pipeline, self.plugins, self.server, self.store = pipeline, plugins, server, store
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=self.port, log_level="warning"))
        threading.Thread(target=self._srv.run, daemon=True).start()
        while not self._srv.started:
            time.sleep(0.05)
        pipeline.start_worker()

    def module(self, pid: str):
        """A loaded plugin's Python module."""
        return sys.modules["recap_plugin_" + pid.replace("-", "_")]

    def loaded(self, pid: str) -> bool:
        run = self.plugins.loaded.get(pid)
        return bool(run) and not run.get("error")

    def get(self, path, **kw):
        return requests.get(self.base + path, timeout=60, **kw)

    def post(self, path, **kw):
        return requests.post(self.base + path, timeout=60, **kw)

    def put(self, path, **kw):
        return requests.put(self.base + path, timeout=60, **kw)

    def delete(self, path, **kw):
        return requests.delete(self.base + path, timeout=60, **kw)

    def fake_recap(self, behaviour: dict):
        """Make the recap's steps swapped for one stand-in step: what each project's job does, by its name
        ("done", "ask": stops to ask, "fail", "slow": runs ten seconds unless paused or cancelled)."""
        pipeline, store = self.pipeline, self.store

        def fake(job):
            what = behaviour.get(store.load(job["project"])["name"], "done")
            time.sleep(0.4)
            if what == "ask":
                pipeline.pause_for(job, "Paused before the script: the character scan found 3 characters to check")
            elif what == "fail":
                raise RuntimeError("the script writer isn't set up")
            elif what == "slow":
                for _ in range(100):
                    pipeline._check(job)
                    time.sleep(0.1)
        pipeline.register_step("test.fake", "Standing in for Make the recap", fake)
        pipeline.STEPS[:] = ["test.fake"]

    def project(self, name: str, **settings) -> str:
        return self.store.create(name, {**self.store.new_project_settings(), **settings})["id"]


def done() -> None:
    print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}", flush=True)
    if "recap.sources" in sys.modules:  # source plugins' processes hold their logs open
        sys.modules["recap.sources"].stop_all()
    for path in made:  # (kept to look at when something failed)
        if fails:
            print(f"(its files: {path})", flush=True)
        else:
            for _ in range(10):  # (a stopped process can hold a file a moment longer)
                shutil.rmtree(path, ignore_errors=True)
                if not path.exists():
                    break
                time.sleep(0.3)
    os._exit(1 if fails else 0)  # (the server's threads don't hold it open)
