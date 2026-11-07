"""Advanced Cutting, with made-up projects rendered for real (chapters of uneven lengths, each its own shade of grey):
cut by chapters, by size and by length, each piece starting on its chapter's first frame and ending on its last, as
long as its chapters, playing cleanly, with its own subtitles and chapter list; a cut made again replacing the pieces
the one before made; a stopped cut keeping them; a chapter too big for a piece cut inside it; the app's own parts cut
and numbered on; a project cut by itself when its video is made; and the settings' checks."""
import json
import re
import subprocess
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import soundfile as sf

from helpers import App, check, done, scratch, until


def main():  # (the video is drawn in several processes: they import this file, and mustn't run it)
    app = App(plugins=["advanced-cutting"])
    pipeline, plugins, store = app.pipeline, app.plugins, app.store
    check("plugin loaded", app.loaded("advanced-cutting"), plugins.loaded.get("advanced-cutting"))
    ac = app.module("advanced-cutting")
    VIDEO = {"resolution": 360, "fps": 10, "video_style": "stills", "orientation": "landscape", "transition_seconds": 0.0,
             "layout": "pages", "shorts": 0, "captions": False, "background_music": False, "video_split": "none"}
    SHADE = {c: 40 + 50 * c for c in range(1, 5)}  # chapter c's grey

    def made_up(name: str, chapters: int = 4, **settings) -> str:
        """Chapters of two panels each, narrated by tones of 0.6 + 0.35 c seconds (so they run unevenly), in grey."""
        pid = app.project(name, **{**VIDEO, **settings})
        d = store.project_dir(pid)
        for sub in ("pages", "panels", "audio"):
            (d / sub).mkdir(parents=True, exist_ok=True)

        def fill(p):
            for c in range(1, chapters + 1):
                cid, grey = f"c{c:04d}", SHADE[c]
                p["chapters"].append({"id": cid, "name": f"Chapter {c}", "source": None})
                cv2.imwrite(str(d / "pages" / f"{cid}_0000.png"), np.full((900, 600, 3), grey, np.uint8))
                p["pages"].append({"file": f"{cid}_0000.png", "name": "001.png", "w": 600, "h": 900, "chapter": cid})
                seconds = 0.6 + 0.35 * c
                for k in range(2):
                    xid = f"{cid}_{k:05d}"
                    cv2.imwrite(str(d / "panels" / f"{xid}.png"), np.full((400, 600, 3), grey, np.uint8))
                    # a rising tone, its own in each panel: no two bits of the sound alike, so each can be found
                    t = np.arange(int(24000 * seconds)) / 24000
                    f0 = 220 + 60 * c + 25 * k
                    sf.write(d / "audio" / f"{xid}.wav", (0.2 * np.sin(2 * np.pi * (f0 * t + 150 * t * t / seconds)))
                             .astype(np.float32), 24000)
                    p["panels"].append({"id": xid, "chapter": cid, "page": 0, "file": f"{xid}.png", "joined": False,
                                        "text": "Line.", "narration": f"Panel {k} of chapter {c}.", "audio": f"{xid}.wav",
                                        "duration": seconds, "enabled": True, "box": [0, 0, 600, 400], "ocr_done": True,
                                        "script_done": True})
        store.update(pid, fill)
        return pid

    def render(pid) -> dict:
        app.post(f"/api/projects/{pid}/run", json={"steps": ["video"]})
        end = time.time() + 300
        while time.time() < end:
            job = pipeline.latest_for(pid)
            if job and job["status"] not in pipeline.ACTIVE:
                return job
            time.sleep(0.3)
        return {}

    def grey(path: Path, at: float) -> float:
        cap = cv2.VideoCapture(str(path))
        cap.set(cv2.CAP_PROP_POS_MSEC, max(at, 0) * 1000)
        ok, img = cap.read()
        cap.release()
        return float(img[150:210, 280:360].mean()) if ok else -1.0

    def records(pid) -> list:
        """Each chapter's exact length and subtitles, as the app drew it."""
        return [json.loads(f.read_text(encoding="utf-8")) for f in sorted((store.project_dir(pid) / "render").glob("c*.json"))]

    def base_of(pid) -> str:
        """What the app named the project's video, less its " - Part N (...)": the project's title (recap.mp4 in
        Recap Studio 0.4.0), which the pieces are named after."""
        return re.sub(r" - Part \d+ \(.*\)$", "", ac.sources(store.load(pid))[0]["video"].stem)

    def pieces_in(folder: Path) -> list[str]:
        return sorted(x.name for x in folder.glob("*.mp4")) if folder.is_dir() else []

    def sound_of(path: Path) -> list:
        """Its sound's packets as ffmpeg reads them, (time, length, CRC), less those marked not to be played."""
        out = subprocess.run([ac._ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:a:0", "-c", "copy", "-f",
                              "framecrc", "-"], capture_output=True, text=True).stdout
        tb, got = 1.0, []
        for line in out.splitlines():
            m = re.match(r"#tb 0: (\d+)/(\d+)", line)
            if m:
                tb = int(m.group(1)) / int(m.group(2))
            elif not line.startswith("#"):
                f = [x.strip() for x in line.split(",")]
                if not next((int(x[2:], 16) for x in f[6:] if x.startswith("F=")), 1) & 4:
                    got.append((int(f[2]) * tb, int(f[3]) * tb, f[5]))
        return got

    def look_at(folder: Path, name: str, chapters: list[int], recs: list, what: str, whole: Path, first: int = 1):
        """A piece holding these chapters of the whole video (whose first chapter is first): as long as they are,
        their shades at both ends, playing, its sound the whole video's from the same moment and all of it, its
        subtitles and chapter list moved to its own start."""
        v = folder / f"{name}.mp4"
        secs = ac.probe_seconds(v)
        want = sum(recs[c - 1]["seconds"] for c in chapters)
        check(f"{what}: runs as long as its chapters", abs(secs - want) < 0.15, (secs, want))
        start = sum(recs[c - 1]["seconds"] for c in range(first, chapters[0]))
        mine, full = sound_of(v), sound_of(whole)
        once = {c for c, n in Counter(c for _, _, c in full).items() if n == 1}
        at = {c: t for t, _, c in full if c in once}
        off = [at[c] - t for t, _, c in mine if c in at]
        check(f"{what}: its sound in step with its picture (each bit the whole video's, from the piece's start)",
              len(off) > 20 and all(abs(x - start) < 0.002 for x in off), (start, sorted({round(x, 4) for x in off})[:6]))
        check(f"{what}: its sound as long as its picture", bool(mine) and mine[0][0] <= 0.001
              and want - 0.001 <= mine[-1][0] + mine[-1][1] <= want + 0.05,
              mine and (mine[0][0], mine[-1][0] + mine[-1][1], want))
        have = {c for _, _, c in mine}
        lost = [round(t, 3) for t, d, c in full if start - 1e-6 <= t and t + d <= start + want + 1e-6 and c not in have]
        check(f"{what}: none of the whole video's sound left out", not lost, lost[:8])
        check(f"{what}: starts on its first chapter's first frame",
              abs(grey(v, 0.05) - SHADE[chapters[0]]) < 20, (grey(v, 0.05), SHADE[chapters[0]]))
        check(f"{what}: ends on its last chapter's last frame",
              abs(grey(v, secs - 0.25) - SHADE[chapters[-1]]) < 20, (grey(v, secs - 0.25), SHADE[chapters[-1]]))
        check(f"{what}: plays cleanly at both ends", not ac.plays(v, 0) and not ac.plays(v, secs - 3))
        subs, offset, want_subs = ac.read_srt(folder / f"{name}.srt"), 0.0, []
        for c in chapters:
            want_subs += [(a + offset, b + offset) for a, b, _ in recs[c - 1]["subs"]]
            offset += recs[c - 1]["seconds"]
        check(f"{what}: its own subtitles, from its start", len(subs) == len(want_subs) and all(
            abs(a - x) < 0.02 and abs(b - y) < 0.02 for (a, b, _), (x, y) in zip(subs, want_subs)), (subs, want_subs))
        marks = ac.read_marks(folder / f"{name} - YouTube chapters.txt")
        check(f"{what}: its own chapter list", [m[0] for m in marks] == [f"Chapter {c}" for c in chapters]
              and marks[0][1] == 0, marks)

    # ---- by chapters, one in each, in the output folder's Pieces ----
    pid = made_up("Cut me")
    job = render(pid)
    check("rendered", job.get("status") == "done", job)
    recs, d = records(pid), store.project_dir(pid)
    pieces = d / "output" / "Pieces"
    src, cut = ac.sources(store.load(pid))[0], base_of(pid)
    whole = src["video"]
    got = ac.cut_project(pid, {**ac.DEFAULTS, "by": "chapters", "chapters": 1})
    names = [f"{cut} - Part {c} (Chapter {c})" for c in range(1, 5)]
    check("by chapters: one piece each, named as the app names its parts", got["pieces"] == [f"{n}.mp4" for n in names]
          and pieces_in(pieces) == sorted(f"{n}.mp4" for n in names), (got, pieces_in(pieces)))
    for c, n in enumerate(names, 1):
        look_at(pieces, n, [c], recs, f"by chapters, part {c}", whole)
    check("the whole video stays", whole.exists())

    # ---- by size: under two chapters' worth (the later chapters run longer, so fewer go in) ----
    listing = ac.list_packets(whole)
    starts = ac.chapter_starts(ac.read_marks(src["chapters"]), listing)
    check("each chapter starts on a keyframe at its exact start", [round(listing.time(k), 2) for _, k in starts]
          == [round(sum(r["seconds"] for r in recs[:c]), 2) for c in range(4)], (starts, [r["seconds"] for r in recs]))
    limit = listing.size(0, starts[2][1]) + 4000
    got = ac.cut_project(pid, {**ac.DEFAULTS, "by": "size", "size_gb": limit / ac.GB})
    files = pieces_in(pieces)
    check("by size: the first piece holds chapters 1 and 2", got["pieces"][0] == f"{cut} - Part 1 (Chapter 1 to Chapter 2).mp4",
          got)
    check("by size: every piece under the size", all((pieces / x).stat().st_size <= limit for x in files),
          [(x, (pieces / x).stat().st_size, limit) for x in files])
    check("a cut made again replaces the one before (its pieces this one didn't make are gone)",
          files == sorted(got["pieces"]) and len(list(pieces.glob("*.srt"))) == len(files), (files, got["pieces"]))
    look_at(pieces, f"{cut} - Part 1 (Chapter 1 to Chapter 2)", [1, 2], recs, "by size, part 1", whole)
    covered = []
    for x in got["pieces"]:
        a, _, b = re.search(r"\((.*)\)\.mp4$", x).group(1).partition(" to ")
        covered += list(range(int(a.split()[-1]), int((b or a).split()[-1]) + 1))
    check("by size: the pieces hold every chapter once, in order", covered == [1, 2, 3, 4], got["pieces"])

    # ---- a video that fits in one piece is one piece ----
    one = scratch()
    got = ac.cut_project(pid, {**ac.DEFAULTS, "by": "size", "size_gb": 1.5, "folder": str(one), "own_folder": False})
    check("a video that fits in one piece: one piece, the whole video",
          got["pieces"] == [f"{cut} - Part 1 (Chapter 1 to Chapter 4).mp4"] and pieces_in(one) == got["pieces"], got)
    look_at(one, f"{cut} - Part 1 (Chapter 1 to Chapter 4)", [1, 2, 3, 4], recs, "the one piece", whole)

    # ---- a stopped cut keeps the pieces of the one before ----
    before = pieces_in(pieces)
    try:
        ac.cut_project(pid, {**ac.DEFAULTS, "by": "chapters", "chapters": 1}, stopped=lambda: True)
        check("a stopped cut stops", False)
    except ac.Stopped:
        check("a stopped cut stops", True)
    check("and keeps the pieces already there", pieces_in(pieces) == before, (pieces_in(pieces), before))

    # ---- by length, into a folder of yours, in one named after the project ----
    mine = scratch()
    hours = (recs[0]["seconds"] + recs[1]["seconds"] + 0.05) / 3600
    got = ac.cut_project(pid, {**ac.DEFAULTS, "by": "length", "hours": hours, "folder": str(mine), "own_folder": True})
    there = mine / "Cut me"
    check("by length: into the folder named after the project", got["folder"] == str(there)
          and pieces_in(there) == sorted(got["pieces"]), got)
    check("by length: the first piece holds chapters 1 and 2, each piece within the length",
          got["pieces"][0] == f"{cut} - Part 1 (Chapter 1 to Chapter 2).mp4"
          and all(ac.probe_seconds(there / x) <= hours * 3600 + 0.15 for x in got["pieces"]), got)
    check("the Pieces folder's own cut is left as it was", pieces_in(pieces) == before)

    # ---- a chapter too big for a piece is cut inside it, at keyframes ----
    fake = ac.Listing()
    fake.keys = [(float(t), 100 * t, 10 * t) for t in range(20)]
    fake.sound += [(float(t), 10 * t, t) for t in range(20)]
    fake.picture, fake.frames, fake.heard, fake.packets, fake.end = 2000, 200, 200, 20, 20.0
    each = fake.size(0, 1) - ac.INDEX[2]
    chs = [("A", 0), ("B", 10)]
    s, k, made = {**ac.DEFAULTS, "by": "size"}, 0, []
    while k < 20:
        piece = ac.next_piece(fake, chs, k, s, ac.INDEX[2] + 5 * each)
        made.append((ac.span_name(chs, piece), piece["a"], piece["b"], [x for x, _ in piece["names"]]))
        k = piece["b"]
    check("a chapter too big for one piece: cut inside it, the next piece 'continued'", made == [
        ("A", 0, 5, ["A"]), ("A continued", 5, 10, ["A (continued)"]), ("B", 10, 15, ["B"]),
        ("B continued", 15, 20, ["B (continued)"])], made)
    piece = ac.next_piece(fake, chs, 0, s, ac.INDEX[2] + 15 * each)
    check("and whole chapters still go together when they fit", (piece["b"], ac.span_name(chs, piece)) == (10, "A"), piece)

    # ---- the app's own parts: each cut, numbered on ----
    split = made_up("Split", video_split="chapters", video_split_chapters=2)
    job = render(split)
    check("rendered in two parts", job.get("status") == "done" and len(store.load(split)["output"]["parts"]) == 2, job)
    got = ac.cut_project(split, {**ac.DEFAULTS, "by": "chapters", "chapters": 1})
    check("the app's parts cut, the pieces numbered on across them",
          got["pieces"] == [f"{base_of(split)} - Part {c} (Chapter {c}).mp4" for c in range(1, 5)], got)
    look_at(store.project_dir(split) / "output" / "Pieces", f"{base_of(split)} - Part 3 (Chapter 3)", [3], records(split),
            "the second part's first piece", store.project_dir(split) / store.load(split)["output"]["parts"][1]["video"], 3)

    # ---- the settings, from the page ----
    base = "/api/plugins/advanced-cutting"
    r = app.put(f"{base}/settings", json={"changes": {"by": "chapters", "chapters": 1, "size_gb": 0.001}})
    check("settings saved, kept in bounds", r.ok and r.json()["settings"]["by"] == "chapters"
          and r.json()["settings"]["size_gb"] == 0.05, r.text)
    check("a folder that isn't a full path refused",
          app.put(f"{base}/settings", json={"changes": {"folder": "Pieces here"}}).status_code == 400)
    check("another web page can't set where the pieces go", app.put(f"{base}/settings", json={"changes": {
        "folder": str(mine)}}, headers={"Origin": "https://example.com"}).status_code == 403)
    check("nor start a cut", app.post(f"{base}/cut/{pid}", headers={"Origin": "https://example.com"}).status_code == 403)
    check("a project with no video can't be cut", app.post(f"{base}/cut/{app.project('No video')}").status_code == 400)
    check("nor stop one", app.post(f"{base}/stop", headers={"Origin": "https://example.com"}).status_code == 403)
    r = app.get(f"{base}/status", params={"project": pid})
    check("its status lists the project's last cut", r.ok and r.json()["project"]["has_video"]
          and len(r.json()["project"]["pieces"]) == len(before), r.text)

    # ---- a folder of yours that isn't there (a share not connected, or not mounted on a server) ----
    gone = mine / "not there"
    r = app.put(f"{base}/settings", json={"changes": {"folder": str(gone)}})
    check("a folder that isn't there refused, not made", r.status_code == 400 and "There's no folder" in r.text
          and not gone.exists(), r.text)
    going = mine / "going"
    going.mkdir()
    check("a folder that's there taken", app.put(f"{base}/settings", json={"changes": {"folder": str(going)}}).ok)
    going.rmdir()
    check("the other settings still change once it's gone",
          app.put(f"{base}/settings", json={"changes": {"by": "chapters"}}).ok)
    try:
        ac.cut_project(pid, {**ac.DEFAULTS, "folder": str(going)})
        check("a cut into it stops, with why", False)
    except RuntimeError as e:
        check("a cut into it stops, with why", "There's no folder" in str(e) and not going.exists(), e)
    check("back to the project's own folder", app.put(f"{base}/settings", json={"changes": {"folder": ""}}).ok)

    # ---- as a server (Recap Studio Server): its page from its own address, and the pieces saved from the page ----
    on_server = ac._on_server
    ac._on_server = lambda: True
    host = f"recap.example:{app.port}"
    check("on a server, its own page (its own address) may change the settings", app.put(
        f"{base}/settings", json={"changes": {"by": "chapters"}}, headers={"Origin": f"http://{host}", "Host": host}).ok)
    check("behind a proxy too (X-Forwarded-Host)", app.put(f"{base}/settings", json={"changes": {"by": "chapters"}},
          headers={"Origin": "https://recap.example.com", "X-Forwarded-Host": "recap.example.com"}).ok)
    check("another address still can't", app.put(f"{base}/settings", json={"changes": {"by": "chapters"}},
          headers={"Origin": "http://other.example", "Host": host}).status_code == 403)
    st = app.get(f"{base}/status", params={"project": pid}).json()
    first = st["project"]["pieces"][0]
    check("its status says it's a server, and each piece's subtitles and chapter list", st["server"] is True
          and first["srt"] == first["name"][:-4] + ".srt"
          and first["chapters"] == first["name"][:-4] + " - YouTube chapters.txt", st)
    r = app.get(f"{base}/piece/{pid}", params={"name": first["name"]})
    check("a piece saved from the page, whole", r.ok and len(r.content) == first["size"] and r.headers.get(
        "content-type") == "video/mp4" and r.headers.get("content-disposition", "").startswith("attachment"), r.headers)
    r = app.get(f"{base}/piece/{pid}", params={"name": first["name"]}, headers={"Range": "bytes=0-99"})
    check("in parts too (a big piece's download picked up again)", r.status_code == 206 and len(r.content) == 100,
          r.status_code)
    r = app.get(f"{base}/piece/{pid}", params={"name": first["srt"]})
    check("its subtitles too", r.ok and r.text.startswith("1"), r.text[:40])
    for bad in ("../project.json", f"../{whole.name}", whole.name, "nothing.mp4"):
        check(f"nothing else ({bad})", app.get(f"{base}/piece/{pid}", params={"name": bad}).status_code == 404)
    ac._on_server = on_server

    # ---- the Video tab's Cut, and a project cut by itself when its video is made ----
    r = app.post(f"{base}/cut/{pid}")
    check("Cut starts a cut", r.ok, r.text)
    check("it's done on its own thread, and logged", until(lambda: not ac._now and not ac._waiting and any(
        x["project"] == "Cut me" and x["note"].startswith("Cut into 4 pieces") for x in ac._read("log.json", [])), 60),
        ac._read("log.json", [])[:2])
    check("as Settings say (one chapter in each), replacing the cut before",
          pieces_in(pieces) == sorted(f"{n}.mp4" for n in names), pieces_in(pieces))
    auto = made_up("Auto", 2, **{"advanced-cutting.auto": "yes"})
    job = render(auto)
    folder = store.project_dir(auto) / "output" / "Pieces"
    check("a project set to: cut by itself when its video is made", job.get("status") == "done" and until(
        lambda: pieces_in(folder) == [f"{base_of(auto)} - Part {c} (Chapter {c}).mp4" for c in (1, 2)], 60),
        pieces_in(folder))
    other = made_up("Not auto", 1, **{"advanced-cutting.auto": "no"})  # (New project starts from the last one's)
    render(other)
    time.sleep(1.5)
    check("one that isn't set to isn't", not (store.project_dir(other) / "output" / "Pieces").exists())
    done()


if __name__ == "__main__":
    main()
