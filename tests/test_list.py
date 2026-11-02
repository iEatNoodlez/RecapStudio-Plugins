"""The plugin list itself: index.json is what tools/build.py makes from features/ and sources/ (nobody forgot to build
after a change), its files match their checksums, every plugin's details are ones the app takes, and every plugin
loads in the app with nothing going wrong, its page files served."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from helpers import APP, ROOT, App, check, done, scratch

index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
entries = {p["id"]: p for p in index["plugins"]}
features = sorted(d.name for d in (ROOT / "features").iterdir() if (d / "plugin.json").is_file())
sources = sorted(f.stem for f in (ROOT / "sources").glob("*.py") if not f.name.startswith("_"))

# ---- index.json is up to date: built again in a copy, it comes out the same ----
copy = scratch("recap-list-")
for name in ("features", "sources", "tools"):
    shutil.copytree(ROOT / name, copy / name, ignore=shutil.ignore_patterns("__pycache__"))
subprocess.run([sys.executable, str(copy / "tools" / "build.py")], check=True, capture_output=True)
rebuilt = {p["id"]: p for p in json.loads((copy / "index.json").read_text(encoding="utf-8"))["plugins"]}
check("every plugin is on the list", sorted(entries) == sorted(features + sources), (sorted(entries), features + sources))
for pid, entry in rebuilt.items():
    check(f"{pid}: index.json is up to date (run tools/build.py after a change)", entries.get(pid) == entry,
          (entries.get(pid, {}).get("sha256"), entry["sha256"]))
for pid, entry in entries.items():
    f = ROOT / entry["file"]
    check(f"{pid}: its file matches the checksum", f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest() == entry["sha256"])
check("no old zips left in dist", sorted(f.name for f in (ROOT / "dist").glob("*.zip"))
      == sorted(Path(e["file"]).name for e in entries.values() if e["kind"] == "feature"))

# ---- in the app ----
app = App(plugins=features, sources=sources)
from recap import plugins as app_plugins, sources as app_sources  # noqa: E402

for pid in features:
    info = json.loads((ROOT / "features" / pid / "plugin.json").read_text(encoding="utf-8"))
    check(f"{pid}: written for a plugin version this app gives", info["api"] <= app_plugins.API, info["api"])
    check(f"{pid}: loads", app.loaded(pid), app_plugins.loaded.get(pid, {}).get("error"))
    if (ROOT / "features" / pid / "web" / "plugin.js").is_file():
        r = app.get(f"/plugin-web/{pid}/plugin.js")
        check(f"{pid}: its page script is served", r.status_code == 200 and len(r.content) > 100)
listed = {p["id"]: p for p in app_sources.plugins()}
for sid in sources:
    check(f"{sid}: a source the app takes", sid in listed and not listed[sid].get("error"), listed.get(sid))
st = app.get("/api/plugins").json()
check("Settings → Plugins lists them all, running", sorted(p["id"] for p in st["plugins"] if p["running"]) == features,
      [(p["id"], p["running"], p["error"]) for p in st["plugins"]])
print(f"(against {APP})")
done()
