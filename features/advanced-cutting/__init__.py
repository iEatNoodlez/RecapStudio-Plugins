"""Advanced Cutting, a Recap Studio feature plugin: a finished video cut into pieces, by size (each under so many GB),
by chapters (so many in each) or by length (so many hours), each starting where a chapter starts, with its own
subtitles and YouTube chapter list. Nothing is encoded again: the pieces are copied out of the video as they are
(ffmpeg's stream copy), so even a 25-hour video is cut in a few minutes. The whole video stays as it is.

- When: the Video tab's Cut the video… (choose how, then Cut), or each time a project's video is made, when its
  Project settings say so (advanced-cutting.auto).
- Where: the project's output folder (in Pieces), or a folder of yours (in a folder named after the project, if you
  like). A cut made again replaces the pieces it made there before.
- Finding the cuts: ffmpeg lists the video's packets (the size of each frame and each bit of sound, and which frames
  a piece can start on: the keyframes). The app draws each chapter on its own, so each starts on a keyframe: the last
  one in the second its mark in the YouTube chapter list is rounded down from. A piece's size is its packets' plus its
  own index; each piece is checked once it's cut (its size, its length, and that it plays at both ends), and cut
  again with less in it if it came out over.
- Cutting: the picture at those keyframes (segment), then each piece's sound put with it from the same moment to the
  same length, read on its own (with_sound): cut together, the end of each piece's sound would go to the next.
- A chapter too big (or too long) for one piece is cut inside it, at a keyframe, and the next piece says "continued".
- With the app's own parts (Split into parts), each part is cut, and the pieces are numbered on across them.
- On a server (Recap Studio Server), the page saves the pieces from the server (/piece). A folder of yours must be
  there already (a share that isn't mounted isn't made again on the server's own disk).

Cutting runs on a thread of its own, one video after another, so the next job doesn't wait for it. Settings, the
pieces each project's last cut made (so a cut made again replaces them) and what happened are kept in the plugin's
data folder (settings.json, made.json, log.json).
"""
import bisect
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from fastapi import Body, HTTPException, Request
from fastapi.responses import FileResponse

from recap import pipeline, store, video

GB = 1_000_000_000            # a GB here is 10^9 bytes, so a piece is under its size however GB are counted
LOG = 40
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULTS = {"by": "size", "size_gb": 1.5, "chapters": 10, "hours": 4.0,
            "folder": "", "own_folder": True, "srt": True, "chapter_list": True}
BYS = ("size", "chapters", "length")
RANGES = {"size_gb": (0.05, 1000.0), "chapters": (1, 10000), "hours": (0.05, 1000.0)}
INDEX = (20, 10, 1 << 16)     # a piece's own index: bytes for each frame, each bit of sound, and the rest (a guess
                              # on the high side; each piece is checked once it's cut)
TRIES = 6                     # cuts of one piece that came out over its size, before giving up
_work: "queue.Queue[str]" = queue.Queue()
_lock = threading.Lock()
_waiting: list[str] = []      # projects queued, in order
_now: dict = {}               # the cut being made: {"project", "name", "message", "progress"}
_stop = threading.Event()
_plugin = None
_key = "advanced-cutting.auto"


class Stopped(Exception):
    pass


# ---- its files -----------------------------------------------------------------------------------------------------

def _read(name: str, default):
    try:
        found = json.loads((_plugin.data_dir / name).read_text(encoding="utf-8"))
        return {**default, **found} if isinstance(default, dict) else found
    except (OSError, ValueError):
        return json.loads(json.dumps(default))


def _write(name: str, data) -> None:
    (_plugin.data_dir / name).write_text(json.dumps(data, indent=1), encoding="utf-8")


def settings() -> dict:
    return _read("settings.json", DEFAULTS)


def _log(project: str, note: str) -> None:
    with _lock:
        log = _read("log.json", [])
        log.insert(0, {"at": time.time(), "project": project, "note": note})
        _write("log.json", log[:LOG])


def _safe(name: str, longest: int = 150) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:longest].strip(" .")


def _size(n: int) -> str:
    return f"{n / GB:.2f} GB" if n >= 0.1 * GB else f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} KB"


def _clock(seconds: float, hours: bool) -> str:
    s = int(seconds)  # (rounded down, as the app writes them: a chapter's mark is never after its start)
    return f"{s // 3600}:{s // 60 % 60:02}:{s % 60:02}" if hours else f"{s // 60}:{s % 60:02}"


# ---- what the video is made of ----------------------------------------------------------------------------------------

class Listing:
    """What a cut needs from a video's packets, kept small: each keyframe's time with the picture's bytes and frames
    before it (in the file's order), and the sound's bytes and packets before each second or so."""

    def __init__(self):
        self.keys: list[tuple[float, int, int]] = []          # (seconds, picture bytes before it, frames before it)
        self.sound: list[tuple[float, int, int]] = [(-1e9, 0, 0)]
        self.picture = self.frames = self.heard = self.packets = 0
        self.end = 0.0

    def time(self, k: int) -> float:
        return self.keys[k][0] if k < len(self.keys) else self.end

    def before(self, k: int) -> tuple[int, int, int]:
        """(picture bytes, frames, sound bytes and packets) before keyframe k (len(keys): the end)."""
        if k >= len(self.keys):
            return self.picture, self.frames, self.heard, self.packets
        t, b, n = self.keys[k]
        i = bisect.bisect_right(self.sound, (t, float("inf"), 0)) - 1
        return b, n, self.sound[i][1], self.sound[i][2]

    def size(self, a: int, b: int) -> int:
        """About how big a piece from keyframe a to keyframe b would be, with its own index."""
        pa, fa, ha, sa = self.before(a)
        pb, fb, hb, sb = self.before(b)
        return (pb - pa) + (hb - ha) + INDEX[0] * (fb - fa) + INDEX[1] * (sb - sa) + INDEX[2]


def _ffmpeg() -> str:
    ffmpeg = video.find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg isn't installed")
    return ffmpeg


def _drain(stream, into: list) -> threading.Thread:
    t = threading.Thread(target=lambda: into.append(stream.read().decode(errors="replace")), daemon=True)
    t.start()
    return t


def list_packets(path: Path, report=None, stopped=None) -> Listing:
    """The video's packets, from ffmpeg's framecrc (each one's stream, time, length and size; a frame a piece can't
    start on is marked F=0x0). The whole file is read, without decoding it."""
    proc = subprocess.Popen([_ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                             "-f", "framecrc", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=NO_WINDOW)
    err: list = []
    reader = _drain(proc.stderr, err)
    out, tb, n, total = Listing(), {}, 0, probe_seconds(path)
    try:
        for raw in proc.stdout:  # (ended by ffmpeg ending)
            if raw[:1] == b"#":
                m = re.match(rb"#tb (\d+): (\d+)/(\d+)", raw)
                if m:
                    tb[int(m.group(1))] = int(m.group(2)) / int(m.group(3))
                continue
            f = raw.split(b",")
            if len(f) < 5 or not f[0].strip().isdigit():
                continue
            stream, pts, length, size = int(f[0]), int(f[2]) * tb[int(f[0])], int(f[3]), int(f[4])
            flags = next((x.strip()[2:] for x in f[6:] if x.strip().startswith(b"F=")), None)
            if stream == 0:
                if flags is None or int(flags, 16) & 1:
                    out.keys.append((pts, out.picture, out.frames))
                out.picture += size
                out.frames += 1
                out.end = max(out.end, pts + length * tb[0])
            else:
                if pts >= out.sound[-1][0] + 1.0:
                    out.sound.append((pts, out.heard, out.packets))
                out.heard += size
                out.packets += 1
            n += 1
            if n % 20000 == 0:
                if stopped and stopped():
                    raise Stopped()
                if report and total:
                    report(min(1.0, pts / total))
    except BaseException:  # (stopped, or something it said that can't be read: ffmpeg isn't left writing)
        proc.kill()
        proc.wait()
        raise
    proc.wait()
    reader.join(5)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg couldn't read the video: {''.join(err).strip()[-300:]}")
    if not out.keys:
        raise RuntimeError("The video has no picture to cut")
    out.keys.sort()
    return out


def probe_seconds(path: Path) -> float:
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace",
                       creationflags=NO_WINDOW)
    d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else 0.0


def plays(path: Path, at: float) -> str:
    """'' when three seconds from there decode cleanly, else what ffmpeg said."""
    r = subprocess.run([_ffmpeg(), "-v", "error", "-ss", f"{max(at, 0.0):.3f}", "-i", str(path), "-t", "3", "-f", "null",
                        "-"], capture_output=True, text=True, errors="replace", creationflags=NO_WINDOW)
    return "" if r.returncode == 0 and not r.stderr.strip() else (r.stderr.strip() or "it didn't play")[-200:]


# ---- where the chapters start, and the pieces --------------------------------------------------------------------------

def read_marks(path: Path | None) -> list[tuple[str, int]]:
    """The YouTube chapter list's (name, second) marks."""
    if not path or not path.exists():
        return []
    marks = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"\s*(?:(\d+):)?(\d+):(\d\d)\s+(.*\S)", line)
        if m:
            marks.append((m.group(4), int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))))
    return marks


def chapter_starts(marks: list, listing: Listing) -> list[tuple[str, int]]:
    """Each chapter's (name, the keyframe it starts on): the last keyframe in the second its mark was rounded down
    from (its first frame is one, and the keyframes after it come more than a second later); with none there (a
    video encoded again since), the last keyframe before. A chapter with no keyframe of its own goes with the one
    before it."""
    times = [k[0] for k in listing.keys]
    out: list[tuple[str, int]] = []
    for name, m in marks:
        k = 0 if not out else bisect.bisect_left(times, m + 1.01) - 1
        if out and k <= out[-1][1]:
            continue
        out.append((name, max(k, 0)))
    return out or [("", 0)]


def next_piece(listing: Listing, chapters: list, k0: int, s: dict, limit: int | None) -> dict:
    """The next piece from keyframe k0: as many whole chapters as fit; when not even the chapter k0 is in fits, as
    much of it as fits (at least to the next keyframe)."""
    end = len(listing.keys)
    starts = [k for _, k in chapters]
    ci = bisect.bisect_right(starts, k0) - 1
    stop = lambda i: starts[i + 1] if i + 1 < len(starts) else end  # noqa: E731  (where chapter i ends)
    if s["by"] == "chapters":
        b = stop(min(ci + int(s["chapters"]) - 1, len(starts) - 1))
    else:
        fits = ((lambda a, b: listing.size(a, b) <= limit) if s["by"] == "size" else
                (lambda a, b: listing.time(b) - listing.time(a) <= float(s["hours"]) * 3600 + 0.001))
        j = ci
        while j < len(starts) and fits(k0, stop(j)):
            j += 1
        if j > ci:
            b = stop(j - 1)
        else:  # the chapter doesn't fit in one piece: cut it at the last keyframe that fits
            b = k0 + 1
            while b + 1 < stop(ci) and fits(k0, b + 1):
                b += 1
    last = bisect.bisect_right(starts, b - 1) - 1
    first = chapters[ci][0]
    names = [(first + (" (continued)" if first and k0 != starts[ci] else ""), 0.0)]
    names += [(chapters[i][0], listing.time(starts[i]) - listing.time(k0)) for i in range(ci + 1, last + 1)]
    return {"a": k0, "b": b, "first": ci, "last": last, "continued": k0 != starts[ci], "names": names}


def span_name(chapters: list, piece: dict) -> str:
    first = chapters[piece["first"]][0]
    if not first:
        return ""
    first += " continued" if piece["continued"] else ""
    return first if piece["last"] == piece["first"] else f"{first} to {chapters[piece['last']][0]}"


def read_srt(path: Path | None) -> list[tuple[float, float, str]]:
    if not path or not path.exists():
        return []
    out = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")):
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            m = re.match(r"(\d+):(\d\d):(\d\d)[,.](\d{3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{3})", line.strip())
            if m:
                g = [int(x) for x in m.groups()]
                out.append((g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000,
                            "\n".join(lines[i + 1:])))
                break
    return out


def _srt_time(t: float) -> str:
    ms = round(max(t, 0.0) * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def srt_for(subs: list, a: float, b: float) -> str:
    """The subtitles from a to b seconds, from 0 (a line cut through is kept, cut to the piece)."""
    lines = [(max(x - a, 0.0), min(y, b) - a, text) for x, y, text in subs if y > a + 0.05 and x < b - 0.05]
    return "\n".join(f"{n}\n{_srt_time(x)} --> {_srt_time(y)}\n{text}\n" for n, (x, y, text) in enumerate(lines, 1))


# ---- cutting ----------------------------------------------------------------------------------------------------------

def _run(cmd: list, stopped, report=None, seconds: float = 0.0) -> None:
    """ffmpeg, following how far it's got (report(fraction) of seconds) and stopping it when stopped()."""
    proc = subprocess.Popen([cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    err: list = []
    reader = _drain(proc.stderr, err)

    def follow():
        for line in proc.stdout:
            key, _, value = line.decode(errors="replace").strip().partition("=")
            if key == "out_time_us" and value.isdigit() and report and seconds:
                report(min(1.0, int(value) / 1e6 / seconds))
    follower = threading.Thread(target=follow, daemon=True)
    follower.start()
    while proc.poll() is None:
        if stopped and stopped():
            proc.kill()
            proc.wait()
            raise Stopped()
        time.sleep(0.2)
    reader.join(5)
    follower.join(5)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg couldn't cut it: {''.join(err).strip()[-300:]}")


def segment(src: Path, listing: Listing, a: int, cuts: list[int], work: Path, stopped=None, report=None,
            sound: Path | None = None) -> list[Path]:
    """src's picture from keyframe a to its end, cut at each of these keyframes, as it is: work/0000.mp4, 0001.mp4…
    ffmpeg's segment muxer starts a new piece at the first keyframe at or after each time, so every packet from a
    keyframe on goes in its piece (a length, -t, would let in the next one's first frames: they're stored ahead of the
    frames shown before them). Each time is halfway from the keyframe before, so a shift of the timestamps doesn't
    move a cut. Only the picture: the muxer would put the sound stored after a cut's keyframe (the end of the piece
    before, as a keyframe is stored ahead of its time) in the next piece. with_sound adds each piece's from sound, the
    whole sound on its own, also written here (when given) as it is."""
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    start = listing.time(a)
    seek = f"{start + 0.001:.3f}"  # (to the keyframe at or before it: a's)
    shift = float(seek) if start > 0 else 0.0
    times = ",".join(f"{(listing.time(k - 1) + listing.time(k)) / 2 - shift:.4f}" for k in cuts)
    _run([_ffmpeg(), "-y", "-v", "error", *(["-ss", seek] if start > 0 else []), "-i", str(src),
          "-map", "0:v:0", "-c", "copy", "-f", "segment", "-segment_format", "mp4", "-reset_timestamps", "1",
          # (with no times, it would cut every 2 seconds: one piece is asked for with a length nothing reaches)
          *(["-segment_times", times] if cuts else ["-segment_time", "1000000000"]), str(work / "%04d.mp4"),
          *(["-map", "0:a:0", "-c", "copy", "-f", "mp4", str(sound)] if sound else [])],
         stopped, report, listing.end - start)
    return sorted(work.glob("*.mp4"))


def with_sound(picture: Path, sound: Path | None, a: float, b: float, out: Path, stopped=None, report=None) -> None:
    """A piece's picture (from segment) with the video's sound from a to b seconds (sound: the whole of it on its
    own, as it is): from the bit (about 1/50 s) a falls in, marked to be played from a, to the last bit that starts
    before b. Each input's first moment is its 0 (a), so the two are in step. Nothing is encoded. (From the video
    itself, a seek would also take the sound stored ahead with a's keyframe, which ffmpeg 4 counts in the length.)"""
    _run([_ffmpeg(), "-y", "-v", "error", "-i", str(picture),
          *([*(["-ss", f"{a:.6f}"] if a > 0 else []), "-t", f"{b - a:.6f}", "-i", str(sound)] if sound else []),
          "-map", "0:v:0", *(["-map", "1:a:0"] if sound else []), "-c", "copy", "-movflags", "+faststart", str(out)],
         stopped, report, b - a)


def _place(tmp: Path, dest: Path) -> None:
    """tmp into dest: moved on the same drive; copied under a passing name and renamed elsewhere (a NAS), so nothing
    watching that folder takes a half-copied file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(tmp, dest)
        return
    except OSError:
        pass
    passing = dest.with_name(dest.name + ".copying")
    shutil.copyfile(tmp, passing)
    os.replace(passing, dest)
    tmp.unlink(missing_ok=True)


def sources(p: dict, lengths: bool = True) -> list[dict]:
    """The project's finished video: each part's video, subtitles and chapter list, as paths, and (lengths) how long
    it runs."""
    d = store.project_dir(p["id"])
    out = p.get("output") or {}
    parts = out.get("parts") or ([{"video": out["video"], "srt": out.get("srt")}] if out.get("video") else [])
    found = []
    for x in parts:
        v = d / x["video"]
        if not v.exists():
            continue
        chapters = d / x["chapters"] if x.get("chapters") else v.with_name(f"{v.stem} - YouTube chapters.txt")
        seconds = float(x.get("seconds") or 0)
        found.append({"video": v, "srt": d / x["srt"] if x.get("srt") else None, "chapters": chapters,
                      "seconds": seconds or (probe_seconds(v) if lengths else 0.0)})
    return found


def destination(p: dict, s: dict) -> Path:
    if not s["folder"]:
        return store.project_dir(p["id"]) / "output" / "Pieces"
    return Path(s["folder"]) / (store.folder_name(p["name"]) if s["own_folder"] else "")


def folder_problem(folder: str) -> str:
    """Why pieces can't go in this folder of yours, or '' if they can. It must be there already: a NAS share that
    isn't connected (or mounted, on a server) isn't made again on this computer's own disk."""
    example = "D:\\Pieces" if os.name == "nt" else "/mnt/nas/Pieces"
    path = Path(folder)
    if not path.is_absolute():
        return f"{folder} isn't a full path ({example}, say)"
    if not path.is_dir():
        return (f"There's no folder {folder} {'on the server' if _on_server() else 'on this computer'}: make it "
                f"first, or connect its drive or share")
    if os.name != "nt" and not os.access(path, os.W_OK | os.X_OK):
        return f"Recap Studio isn't allowed to write in {folder}{_SERVER_USER if _on_server() else ''}"
    return ""


_SERVER_USER = " (the user the server runs as, recap-studio with install.sh, needs to be)"


def _busy(pid: str) -> bool:
    """The project's video is being made again (its file would be replaced under the cut)."""
    job = pipeline.latest_for(pid)
    return bool(job and job["status"] in pipeline.ACTIVE and "video" in (job.get("steps") or []))


def cut_project(pid: str, s: dict, report=None, stopped=None) -> dict:
    """Cut the project's video as s says. Gives {"pieces": [file names], "folder", "biggest", "notes"}."""
    p = store.load(pid)
    parts = sources(p)
    if not parts:
        raise RuntimeError("This project has no video yet")
    if s["folder"] and (why := folder_problem(s["folder"])):
        raise RuntimeError(why)
    dest = destination(p, s)
    work = store.project_dir(pid) / "render" / "cutting"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    limit = int(float(s["size_gb"]) * GB) if s["by"] == "size" else None
    base = re.sub(r" - Part \d+ \(.*\)$", "", parts[0]["video"].stem)
    whole = sum(x["seconds"] for x in parts) or 1.0
    made, notes, biggest, n, done, finished = [], [], 0, 0, 0.0, False
    folder_key = str(dest)
    previous = (_read("made.json", {}).get(pid) or {}).get(folder_key, [])

    def remember(also=()):
        with _lock:
            m = _read("made.json", {})
            m.setdefault(pid, {})[folder_key] = made + [x for x in also if x not in made]
            _write("made.json", m)

    def check_between():
        if stopped and stopped():
            raise Stopped()
        if _busy(pid):
            raise Stopped("the video is being made again")

    try:
        for part in parts:
            share = part["seconds"] / whole
            step = lambda what, f, d0=done, share=share: report and report(what, d0 + share * f)  # noqa: E731
            step("Reading the video", 0.0)
            listing = list_packets(part["video"], lambda f: step("Reading the video", 0.3 * f), stopped)
            marks = read_marks(part["chapters"])
            chapters = chapter_starts(marks, listing)
            if not marks:
                notes.append(f"{part['video'].name} has no chapter list, so it's cut at keyframes only")
            subs = read_srt(part["srt"]) if s["srt"] else []
            sound = work / "sound.m4a" if listing.packets else None  # (the part's sound on its own: segment writes it)
            k, rooms, tries, end = 0, {}, {}, max(listing.end, 0.001)
            while k < len(listing.keys):  # (again from a piece that came out over its size, with less in it)
                check_between()
                plan, kk = [], k
                while kk < len(listing.keys):  # the pieces from k to the end, as planned now
                    plan.append(next_piece(listing, chapters, kk, s, rooms.get(n + len(plan) + 1, limit)))
                    kk = plan[-1]["b"]
                at = listing.time(k)
                files = segment(part["video"], listing, k, [x["a"] for x in plan[1:]], work / "picture", stopped,
                                lambda f, at=at: step("Cutting", 0.3 + 0.35 * (at + f * (end - at)) / end),
                                sound if k == 0 and sound else None)  # (the sound once, on the first go)
                if len(files) != len(plan):
                    raise RuntimeError(f"ffmpeg cut {len(files)} pieces where {len(plan)} were planned")
                for piece, picture in zip(plan, files):
                    check_between()
                    a, b = listing.time(piece["a"]), listing.time(piece["b"])
                    tmp = work / "piece.mp4"
                    with_sound(picture, sound, a, b, tmp, stopped,
                               lambda f, a=a, b=b: step(f"Adding part {n + 1}'s sound", 0.65 + 0.35 * (a + f * (b - a)) / end))
                    picture.unlink()
                    size = tmp.stat().st_size
                    if limit is not None and size > limit and piece["b"] > piece["a"] + 1:
                        tries[n + 1] = tries.get(n + 1, 0) + 1
                        if tries[n + 1] >= TRIES:
                            raise RuntimeError(f"Part {n + 1} came out over {_size(limit)} {TRIES} times")
                        rooms[n + 1] = rooms.get(n + 1, limit) - (size - limit) - (1 << 20)
                        break
                    n += 1
                    if limit is not None and size > limit:
                        notes.append(f"part {n} is {_size(size)}: the video can't be cut any finer there")
                    span = span_name(chapters, piece)
                    name = _safe(f"{base} - Part {n}" + (f" ({span})" if span else ""))
                    step(f"Checking part {n}", 0.3 + 0.7 * b / end)
                    length = probe_seconds(tmp)
                    for t in (0.0, length - 3):
                        wrong = plays(tmp, t)
                        if wrong:
                            raise RuntimeError(f"Part {n} doesn't play at {_clock(t, True)}: {wrong}")
                    if abs(length - (b - a)) > 0.5:
                        raise RuntimeError(f"Part {n} runs {length:.1f} s, not {b - a:.1f} s")
                    _place(tmp, dest / f"{name}.mp4")
                    made.append(f"{name}.mp4")
                    if subs:
                        (work / "piece.srt").write_text(srt_for(subs, a, b), encoding="utf-8")
                        _place(work / "piece.srt", dest / f"{name}.srt")
                        made.append(f"{name}.srt")
                    if s["chapter_list"] and piece["names"][0][0]:
                        hours = (b - a) >= 3600
                        (work / "piece.txt").write_text("".join(f"{_clock(t, hours)} {x}\n" for x, t in piece["names"]),
                                                        encoding="utf-8")
                        _place(work / "piece.txt", dest / f"{name} - YouTube chapters.txt")
                        made.append(f"{name} - YouTube chapters.txt")
                    remember(previous)
                    biggest = max(biggest, size)
                    k = piece["b"]
            done += share
        finished = True
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if finished:  # what the cut before made here and this one didn't (it had more pieces, say) goes
            for old in previous:
                if old not in made:
                    (dest / old).unlink(missing_ok=True)
            remember()
        else:  # stopped or failed: the cut before stays as it was, besides the pieces this one replaced
            remember([x for x in previous if (dest / x).exists()])
    return {"pieces": [x for x in made if x.endswith(".mp4")], "folder": str(dest), "biggest": biggest, "notes": notes}


def _how(s: dict) -> str:
    return {"size": f"each under {s['size_gb']:g} GB",
            "chapters": f"{s['chapters']} chapter{'s' * (s['chapters'] != 1)} in each",
            "length": f"each up to {s['hours']:g} hours"}[s["by"]]


def cut_now(pid: str) -> None:
    """One project's cut, on the worker thread, with what happened logged."""
    s = settings()
    try:
        name = store.load(pid)["name"]
    except Exception:
        return
    _stop.clear()
    _now.update(project=pid, name=name, message="Starting", progress=0.0)

    def report(what, f):
        _now.update(message=what, progress=round(min(1.0, max(0.0, f)), 3))
    try:
        got = cut_project(pid, s, report, _stop.is_set)
        _log(name, f"Cut into {len(got['pieces'])} piece{'s' * (len(got['pieces']) != 1)} ({_how(s)}; the biggest "
                   f"{_size(got['biggest'])}), in {got['folder']}" + "".join(f". {x[:1].upper()}{x[1:]}" for x in got["notes"]))
    except Stopped as e:
        _log(name, f"Stopped{f': {e}' if str(e) else ''}. The pieces made so far are kept")
    except PermissionError as e:
        _log(name, f"Couldn't cut it: Recap Studio isn't allowed to write {e.filename or 'there'}"
                   f"{_SERVER_USER if _on_server() else ''}")
    except Exception as e:
        _log(name, f"Couldn't cut it: {e}")
    finally:
        _stop.clear()
        _now.clear()


def _worker() -> None:
    while True:
        pid = _work.get()
        with _lock:
            if pid not in _waiting:  # (taken off the queue meanwhile)
                continue
            _waiting.remove(pid)
        cut_now(pid)


def queue_cut(pid: str) -> None:
    with _lock:
        if pid in _waiting or _now.get("project") == pid:
            return
        _waiting.append(pid)
    _work.put(pid)


def after_job(job: dict) -> None:
    """When a project's video is made: cut it, if its Project settings say so."""
    if job["status"] != "done" or "video" not in (job.get("steps") or []):
        return
    try:
        wanted = store.load(job["project"])["settings"].get(_key, "no") == "yes"
    except Exception:
        return
    if wanted:
        queue_cut(job["project"])


# ---- the plugin's API --------------------------------------------------------------------------------------------------

def _on_server() -> bool:
    """Recap Studio as a server (0.4 and later): its page comes from its own address, not 127.0.0.1."""
    try:
        from recap import system
    except ImportError:
        return False
    return bool(getattr(system, "SERVER", False))


def _own_page(request: Request) -> None:
    """Pieces are written into a folder on this PC: only the app's own page may say which, never another web page
    (it sends an Origin). A server's page is its own address, behind its login."""
    origin = request.headers.get("origin")
    own = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")} - {""}
    if origin and not re.match(r"https?://(127\.0\.0\.1|localhost)(:\d+)?$", origin) and not (
            _on_server() and urllib.parse.urlparse(origin).netloc in own):
        raise HTTPException(403, "Not from a web page")


def _status(pid: str | None = None) -> dict:
    clock = lambda t: datetime.fromtimestamp(t).strftime("%d %b %H:%M")  # noqa: E731
    out = {"settings": settings(), "windows": os.name == "nt", "server": _on_server(), "now": dict(_now) or None,
           "waiting": [], "log": [{**x, "at": clock(x["at"])} for x in _read("log.json", [])]}
    with _lock:
        waiting = list(_waiting)
    for w in waiting:
        try:
            out["waiting"].append({"project": w, "name": store.load(w)["name"]})
        except Exception:
            pass
    if pid:
        try:
            p = store.load(pid)
        except Exception:
            raise HTTPException(404, "No such project")
        s = settings()
        dest = destination(p, s)
        names = (_read("made.json", {}).get(pid) or {}).get(str(dest), [])
        there = lambda x: x if x in names and (dest / x).is_file() else None  # noqa: E731
        out["project"] = {"has_video": bool(sources(p, lengths=False)), "folder": str(dest), "pieces": [
            {"name": x, "size": (dest / x).stat().st_size, "srt": there(f"{x[:-4]}.srt"),
             "chapters": there(f"{x[:-4]} - YouTube chapters.txt")} for x in names if x.endswith(".mp4") and there(x)]}
    return out


def made_file(pid: str, name: str) -> Path:
    """One of the files the project's last cut made, in the folder Settings name now (nothing else)."""
    try:
        p = store.load(pid)
    except Exception:
        raise HTTPException(404, "No such project")
    dest = destination(p, settings())
    if name not in (_read("made.json", {}).get(pid) or {}).get(str(dest), []) or not (dest / name).is_file():
        raise HTTPException(404, "That piece isn't there")
    return dest / name


def setup(plugin):
    global _plugin, _key
    _plugin = plugin
    _key = plugin.project_setting("auto", "no")
    plugin.on("job_done", after_job)
    threading.Thread(target=_worker, daemon=True, name="advanced-cutting").start()

    @plugin.router.get("/status")
    def status(project: str | None = None):
        return _status(project)

    @plugin.router.put("/settings")
    def save(request: Request, changes: dict = Body(..., embed=True)):
        _own_page(request)
        s = settings()
        for k, v in changes.items():
            if k not in DEFAULTS:
                continue
            if k == "by":
                if v not in BYS:
                    raise HTTPException(400, f"{v} isn't a way to cut")
                s[k] = v
            elif k in RANGES:
                try:
                    low, high = RANGES[k]
                    s[k] = max(low, min(high, int(v) if k == "chapters" else round(float(v), 3)))
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{v} isn't a number")
            elif isinstance(DEFAULTS[k], bool):
                s[k] = bool(v)
            else:
                s[k] = str(v).strip().strip('"')
        if s["folder"] and changes.get("folder") is not None and (why := folder_problem(s["folder"])):
            raise HTTPException(400, why)
        _write("settings.json", s)
        return _status()

    @plugin.router.post("/cut/{pid}")
    def cut_it(pid: str, request: Request):
        """Cut a project's video now, as Settings say (the Video tab's dialog saves its choices there first)."""
        _own_page(request)
        try:
            p = store.load(pid)
        except Exception:
            raise HTTPException(404, "No such project")
        if not sources(p, lengths=False):
            raise HTTPException(400, "This project has no video yet")
        if _busy(pid):
            raise HTTPException(409, "Its video is being made: cut it once that's done")
        queue_cut(pid)
        return _status(pid)

    @plugin.router.get("/piece/{pid}")
    def piece(pid: str, name: str):
        """A piece the project's last cut made, or its subtitles or chapter list, to save (a server's page: the pieces
        are on the server)."""
        return FileResponse(made_file(pid, name), filename=name)

    @plugin.router.post("/stop")
    def stop(request: Request, project: str | None = None):
        """Stop the cut being made (project: that one, or take it off the queue)."""
        _own_page(request)
        with _lock:
            if project and project in _waiting:
                _waiting.remove(project)
                return _status()
        if _now and (not project or _now.get("project") == project):
            _stop.set()
        return _status()
