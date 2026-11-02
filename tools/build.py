"""Build the plugin list: zip each feature plugin into dist\\, and write index.json with every plugin's checksum.

    python tools/build.py           # after changing a plugin (and its version), before committing

The list's entries:
  source plugins   sources\\<id>.py, read without running them (the same CAPITAL settings Recap Studio reads)
  feature plugins  features\\<id>\\ (plugin.json and its files), zipped as dist\\<id>-<version>.zip
The zips are built the same way every time (sorted names, fixed dates), so a plugin that didn't change keeps its
checksum.
"""
import ast
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "Recap Studio's plugins"
SKIP = {"__pycache__", ".DS_Store", "Thumbs.db"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def source_entry(f: Path) -> dict:
    tree = ast.parse(f.read_text(encoding="utf-8"))
    meta = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id.isupper() and n.targets[0].id in
            {"NAME", "VERSION", "SITES", "LANGUAGE", "NSFW", "CHAPTERS"}}
    doc = (ast.get_docstring(tree) or "").split("\n\n")[0].replace("\n", " ")
    return {"id": f.stem, "kind": "source", "name": meta["NAME"], "version": meta["VERSION"],
            "file": f.relative_to(ROOT).as_posix(), "sha256": sha256(f), "summary": _sentence(doc.split(": ", 1)[-1])[:300],
            "sites": meta.get("SITES", []), "language": meta.get("LANGUAGE", ""), "nsfw": bool(meta.get("NSFW")),
            "chapters": meta.get("CHAPTERS", True) is not False}


def feature_entry(folder: Path) -> dict:
    info = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
    if info["id"] != folder.name:
        sys.exit(f"{folder}: its plugin.json says {info['id']}")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / f"{info['id']}-{info['version']}.zip"
    files = sorted(p for p in folder.rglob("*") if p.is_file() and not SKIP & set(p.relative_to(folder).parts))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            zi = zipfile.ZipInfo(p.relative_to(folder).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, p.read_bytes())
    for old in dist.glob(f"{info['id']}-*.zip"):  # earlier versions' zips
        if old != out:
            old.unlink()
    return {"id": info["id"], "kind": "feature", "name": info["name"], "version": info["version"], "api": info["api"],
            "file": out.relative_to(ROOT).as_posix(), "sha256": sha256(out), "summary": info.get("summary", "")}


def main():
    plugins = [feature_entry(d) for d in sorted((ROOT / "features").iterdir()) if (d / "plugin.json").is_file()]
    plugins += [source_entry(f) for f in sorted((ROOT / "sources").glob("*.py")) if not f.name.startswith("_")]
    index = {"name": NAME, "plugins": plugins}
    (ROOT / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for p in plugins:
        print(f"{p['kind']:8} {p['id']:20} {p['version']:8} {p['sha256'][:12]}  {p['file']}")


if __name__ == "__main__":
    main()
