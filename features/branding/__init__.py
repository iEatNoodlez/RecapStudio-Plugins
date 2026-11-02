"""Branding, a Recap Studio feature plugin: a channel's logo over every frame, and an intro and an outro before and
after each part of the video. Set once in Settings → Branding; each project can leave either out (branding.use, in
Project settings: all, logo, clips or none).

- The logo goes through the app's overlay hook (plugin.overlay): drawn into each frame as the chapters are rendered,
  so it's in the Shorts too, and changing it renders the chapters again.
- The intro and outro (a clip, or a picture shown for some seconds) are added on video_rendered, to each part once
  it's joined: each is fitted to the video's frame (black bars where the shapes differ) and encoded with the encoder
  that made the chapters, then the three are joined without encoding the recap's picture again (ffmpeg's concat,
  checked at each join, and encoded again only if a join doesn't decode cleanly). The sound is joined and encoded
  once. The subtitles and the YouTube chapter list are moved along by the intro's length (the first chapter still
  starts at 0:00, as YouTube wants). Shorts (cut before) and Export for editors are left without them.

Its files are kept in the plugin's data folder: logo.png, intro.<ext>, outro.<ext> and settings.json.
"""
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

import cv2
import numpy as np
from fastapi import File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from recap import pipeline, store, video

PICTURES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
CLIPS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".gif"}
LONGEST = 120                 # seconds an intro or outro may run
SIZES = {"small": 0.08, "medium": 0.12, "large": 0.18}
WHERE = ("top-left", "top-right", "bottom-left", "bottom-right")
USES = ("all", "logo", "clips", "none")
DEFAULTS = {"where": "top-right", "size": "medium", "opacity": 0.85, "intro_seconds": 4.0, "outro_seconds": 5.0,
            "names": {}}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_lock = threading.Lock()
_plugin = None
_key = "branding.use"


def settings() -> dict:
    try:
        return {**DEFAULTS, **json.loads((_plugin.data_dir / "settings.json").read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULTS))


def _save(s: dict) -> None:
    (_plugin.data_dir / "settings.json").write_text(json.dumps(s, indent=1), encoding="utf-8")


def file_of(what: str) -> Path | None:
    """The logo, intro or outro, when there is one."""
    return next((f for f in sorted(_plugin.data_dir.glob(f"{what}.*")) if f.suffix != ".part"), None)


def _use(project_settings: dict) -> str:
    use = project_settings.get(_key) or "all"
    return use if use in USES else "all"


def overlay(project_settings: dict) -> dict | None:
    logo = file_of("logo")
    if not logo or _use(project_settings) not in ("all", "logo"):
        return None
    s = settings()
    return {"image": str(logo), "where": s["where"], "width": SIZES.get(s["size"], 0.12), "margin": 0.03,
            "opacity": float(s["opacity"])}


# ---- ffmpeg ----------------------------------------------------------------------------------------------------------

def probe(path: Path) -> dict:
    """What ffmpeg says a file is: {"seconds", "video": bool, "rate", "channels"} (rate None: no sound)."""
    r = subprocess.run([video.find_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       errors="replace", creationflags=NO_WINDOW)
    err = r.stderr
    d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    a = re.search(r"Stream #\S+.*?: Audio: .*?, (\d+) Hz, ([^,]+)", err)
    return {"seconds": int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else 0.0,
            "video": bool(re.search(r"Stream #\S+.*?: Video: ", err)),
            "rate": int(a.group(1)) if a else None,
            "channels": 1 if a and a.group(2).strip() == "mono" else 2}  # (the recap's sound is mono, or stereo with music)


def encoder_of(main: Path, project_settings: dict) -> list:
    """The encoder that made the recap's picture, so the intro joins it cleanly: the CPU's (x264 signs its frames),
    else the GPU's the app starts with."""
    with open(main, "rb") as f:
        head = f.read(16 << 20)
    if b"x264 - core" in head:
        return video.CPU_ENCODER
    return video.chapter_encoder(project_settings)


def _run(cmd: list, seconds: float, report, cancelled) -> None:
    """ffmpeg, following its progress (report(fraction)) and stopping it when cancelled."""
    proc = subprocess.Popen([cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    err = []
    reader = threading.Thread(target=lambda: err.append(proc.stderr.read().decode(errors="replace")), daemon=True)
    reader.start()
    for line in proc.stdout:
        key, _, value = line.decode(errors="replace").strip().partition("=")
        if key == "out_time_us" and value.isdigit() and report:
            report(min(1.0, int(value) / 1e6 / max(seconds, 0.001)))
        if cancelled and cancelled():
            proc.kill()
            proc.wait()
            raise InterruptedError("cancelled")
    proc.wait()
    reader.join(5)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {''.join(err)[-600:]}")


def make_clip(src: Path, seconds: float, W: int, H: int, fps: int, args: list, rate: int, channels: int,
              out: Path, cancelled=None) -> float:
    """The intro or outro as the recap's own kind of video: out.mp4 (picture only, exactly so many frames) and
    out.wav (its sound, exactly as long). Gives its length in seconds."""
    ffmpeg = video.find_ffmpeg()
    picture = src.suffix.lower() in PICTURES
    info = {"seconds": seconds, "rate": None} if picture else probe(src)
    frames = max(1, round(min(LONGEST, info["seconds"]) * fps))
    length = frames / fps
    fit = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
           f"setsar=1,fps={fps},tpad=stop_mode=clone:stop_duration=2,format={video.output_pix_fmt(args)}")
    source = ["-loop", "1", "-i", str(src)] if picture else ["-i", str(src)]
    tries = [args] + ([video.CPU_ENCODER] if video.is_hardware(args) else [])
    for k, enc in enumerate(tries):
        try:
            _run([ffmpeg, "-y", "-loglevel", "error", *source, "-vf", fit, "-frames:v", str(frames), *enc,
                  "-pix_fmt", video.output_pix_fmt(enc), "-an", str(out.with_suffix(".mp4"))], length, None, cancelled)
            break
        except RuntimeError:
            if k + 1 == len(tries):
                raise
    layout = "mono" if channels == 1 else "stereo"
    sound = (["-i", str(src), "-map", "0:a:0"] if info.get("rate") else
             ["-f", "lavfi", "-i", f"anullsrc=r={rate}:cl={layout}"])
    _run([ffmpeg, "-y", "-loglevel", "error", *sound, "-af", f"aresample={rate},apad", "-t", f"{length:.6f}",
          "-ar", str(rate), "-ac", str(channels), str(out.with_suffix(".wav"))], length, None, cancelled)
    return length


def _concat_line(path: Path) -> str:
    return "file '" + path.resolve().as_posix().replace("'", "'\\''") + "'\n"


def _joins_clean(path: Path, at: list[float]) -> bool:
    """Each join decodes without an error (the recap's picture was copied, not encoded again)."""
    for t in at:
        r = subprocess.run([video.find_ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t - 1.5):.3f}", "-i", str(path),
                            "-t", "3", "-map", "0:v", "-f", "null", "-"], capture_output=True, text=True,
                           errors="replace", creationflags=NO_WINDOW)
        if r.returncode != 0 or r.stderr.strip():
            return False
    return True


def join(main: Path, main_seconds: float, clips: list[tuple[str, Path, float]], project_settings: dict, work: Path,
         report=None, cancelled=None) -> dict:
    """[intro,] the recap and [outro,] into main (replaced only once it's all done). clips: [("intro"|"outro",
    file, seconds a picture shows)]. Gives {"intro": seconds, "outro": seconds, "encoded": the picture was encoded
    again}."""
    ffmpeg = video.find_ffmpeg()
    W, H = video.frame_size(project_settings)
    fps = int(project_settings["fps"])
    args = encoder_of(main, project_settings)
    heard = probe(main)
    rate, channels = heard["rate"] or 24000, heard["channels"] if heard["rate"] else 1
    layout = "mono" if channels == 1 else "stereo"
    work.mkdir(parents=True, exist_ok=True)
    made = {}
    for what, src, seconds in clips:
        made[what] = (work / what, make_clip(src, seconds, W, H, fps, args, rate, channels, work / what, cancelled))
    order = [made["intro"][0]] if "intro" in made else []
    order += [None] + ([made["outro"][0]] if "outro" in made else [])
    total = main_seconds + sum(x[1] for x in made.values())
    listing = work / "join.txt"
    listing.write_text("".join(_concat_line(main if x is None else x.with_suffix(".mp4")) for x in order), encoding="utf-8")
    sounds, graph = [], []
    for k, x in enumerate(order, 1):  # input 0 is the pictures; then each piece's sound, in order
        if x is None:
            sounds += ["-i", str(main)] if heard["rate"] else ["-f", "lavfi", "-t", f"{main_seconds:.6f}", "-i",
                                                             f"anullsrc=r={rate}:cl={layout}"]
            graph.append(f"[{k}:a]aformat=sample_fmts=fltp:sample_rates={rate}:channel_layouts={layout},apad,"
                         f"atrim=duration={main_seconds:.6f}[a{k}]")
        else:
            sounds += ["-i", str(x.with_suffix(".wav"))]
            graph.append(f"[{k}:a]aformat=sample_fmts=fltp:sample_rates={rate}:channel_layouts={layout}[a{k}]")
    graph.append("".join(f"[a{k}]" for k in range(1, len(order) + 1)) + f"concat=n={len(order)}:v=0:a=1[a]")
    tmp = main.with_name(main.stem + ".branding.mp4")
    sound_out = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)]
    joins = [made["intro"][1]] if "intro" in made else []
    joins += [made["intro"][1] + main_seconds if "intro" in made else main_seconds] if "outro" in made else []
    encoded = False
    try:
        _run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), *sounds,
              "-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[a]", "-c:v", "copy", *sound_out],
             total, report, cancelled)
        if not _joins_clean(tmp, joins):  # the encoders didn't match after all: encode the picture again
            encoded = True
            pictures = []
            for x in order:
                pictures += ["-i", str(main if x is None else x.with_suffix(".mp4"))]
            n = len(order)
            vgraph = "".join(f"[{k}:v]" for k in range(n)) + f"concat=n={n}:v=1:a=0[v]"
            agraph = [re.sub(r"\[(\d+):a\]", lambda m: f"[{int(m.group(1)) + n - 1}:a]", g) for g in graph]
            _run([ffmpeg, "-y", "-loglevel", "error", *pictures, *sounds, "-filter_complex", ";".join([vgraph, *agraph]),
                  "-map", "[v]", "-map", "[a]", *args, "-pix_fmt", video.output_pix_fmt(args), *sound_out],
                 total, report, cancelled)
        tmp.replace(main)
    finally:
        tmp.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)
    return {"intro": made.get("intro", (None, 0.0))[1], "outro": made.get("outro", (None, 0.0))[1], "encoded": encoded}


# ---- the subtitles and the chapter list, moved along -------------------------------------------------------------------

def _srt_time(t: float) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def shift_srt(path: Path, by: float) -> None:
    def moved(m):
        h, mi, s, ms = (int(x) for x in m.groups())
        return _srt_time(h * 3600 + mi * 60 + s + ms / 1000 + by)
    path.write_text(re.sub(r"(\d+):(\d\d):(\d\d),(\d{3})", moved, path.read_text(encoding="utf-8")), encoding="utf-8")


def shift_chapters(path: Path, by: float, total: float) -> None:
    """The YouTube chapter list: every mark after the first moved along (the first stays at 0:00)."""
    out = []
    for k, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        m = re.match(r"(?:(\d+):)?(\d+):(\d\d) (.*)", line)
        if not m:
            continue
        t = (int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) + (by if k else 0)
        h, rest = divmod(int(t), 3600)
        mi, s = divmod(rest, 60)
        out.append((f"{h}:{mi:02}:{s:02}" if total >= 3600 else f"{mi}:{s:02}") + f" {m.group(4)}\n")
    path.write_text("".join(out), encoding="utf-8")


def on_rendered(project: dict, parts: list, shorts: list) -> None:
    """Once the video's parts are joined: the intro and outro added to each."""
    s, pid = settings(), project["id"]
    if _use(project["settings"]) not in ("all", "clips") or not parts:
        return
    clips = [(what, f, float(s[f"{what}_seconds"])) for what in ("intro", "outro") if (f := file_of(what))]
    if not clips:
        return
    job = pipeline.latest_for(pid)
    job = job if job and job["status"] == "running" else None
    note = getattr(pipeline, "_add_note", lambda j, n: None)
    d = store.project_dir(pid)
    by_name = {Path(x["video"]).name: x for x in (project.get("output") or {}).get("parts") or []}
    done = {}
    try:
        for k, main in enumerate(map(Path, parts)):
            part = by_name.get(main.name, {})
            words = "the intro and outro" if len(clips) == 2 else f"the {clips[0][0]}"

            def report(f, k=k, words=words):
                if job:
                    job["message"] = f"Adding {words}" + (f" to part {k + 1} of {len(parts)}" if len(parts) > 1 else "") \
                                     + f": {int(f * 100)}%"
            report(0.0)
            got = join(main, float(part.get("seconds") or probe(main)["seconds"]), clips, project["settings"],
                       d / "render" / "branding", report, (lambda: bool(job.get("cancel"))) if job else None)
            if part.get("srt") and (d / part["srt"]).exists():
                shift_srt(d / part["srt"], got["intro"])
            total = float(part.get("seconds") or 0) + got["intro"] + got["outro"]
            if part.get("chapters") and (d / part["chapters"]).exists():
                shift_chapters(d / part["chapters"], got["intro"], total)
            done[main.name] = got
    except InterruptedError:
        if job:
            note(job, "The intro and outro weren't added: cancelled")
    except Exception as e:
        if job:
            note(job, f"The intro and outro weren't added ({e})")
        raise
    finally:
        if done:
            def apply(p):
                for x in (p.get("output") or {}).get("parts") or []:
                    got = done.get(Path(x["video"]).name)
                    if got:
                        x["seconds"] = round(float(x.get("seconds") or 0) + got["intro"] + got["outro"], 1)
            store.update(pid, apply)
            if job:
                again = sum(1 for g in done.values() if g["encoded"])
                note(job, "With " + ("the intro and outro" if len(clips) == 2 else f"the {clips[0][0]}")
                     + " (Branding)" + (f"; {again} part{'s' * (again != 1)} encoded again to join them" if again else ""))


# ---- the plugin's API --------------------------------------------------------------------------------------------------

def _status() -> dict:
    s = settings()
    files = {}
    for what in ("logo", "intro", "outro"):
        f = file_of(what)
        files[what] = {"name": s["names"].get(what) or f.name, "picture": f.suffix.lower() in PICTURES,
                       "stamp": int(f.stat().st_mtime)} if f else None
    return {"settings": s, "files": files, "sizes": list(SIZES), "where": list(WHERE)}


def setup(plugin):
    global _plugin, _key
    _plugin = plugin
    _key = plugin.project_setting("use", "all")
    plugin.overlay(overlay)
    plugin.on("video_rendered", on_rendered)

    @plugin.router.get("/status")
    def status():
        return _status()

    @plugin.router.put("/settings")
    def save(changes: dict):
        s = settings()
        if changes.get("where") in WHERE:
            s["where"] = changes["where"]
        if changes.get("size") in SIZES:
            s["size"] = changes["size"]
        for k, low, high in (("opacity", 0.2, 1.0), ("intro_seconds", 1.0, 30.0), ("outro_seconds", 1.0, 30.0)):
            if k in changes:
                try:
                    s[k] = max(low, min(high, float(changes[k])))
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{changes[k]} isn't a number")
        with _lock:
            _save(s)
        return _status()

    @plugin.router.post("/file/{what}")
    async def upload(what: str, file: UploadFile = File(...)):
        if what not in ("logo", "intro", "outro"):
            raise HTTPException(404)
        ext = Path(file.filename or "").suffix.lower()
        data = await file.read()
        part = plugin.data_dir / f"{what}.part"
        if what == "logo" or ext in PICTURES:
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
            if img is None:
                raise HTTPException(400, f"{file.filename} isn't a picture this app can read (PNG, JPEG, WebP)")
            if img.shape[1] > 1920:  # (a logo or a title card never needs more)
                img = cv2.resize(img, (1920, round(img.shape[0] * 1920 / img.shape[1])), interpolation=cv2.INTER_AREA)
            part.write_bytes(cv2.imencode(".png", img)[1].tobytes())
            final = plugin.data_dir / f"{what}.png"
        elif ext in CLIPS:
            part.write_bytes(data)
            info = probe(part)
            if not info["video"]:
                part.unlink(missing_ok=True)
                raise HTTPException(400, f"{file.filename} has no video in it")
            if info["seconds"] > LONGEST:
                part.unlink(missing_ok=True)
                raise HTTPException(400, f"{file.filename} runs {info['seconds']:.0f} seconds: an intro or outro can "
                                         f"run {LONGEST} at most")
            final = plugin.data_dir / f"{what}{ext}"
        else:
            raise HTTPException(400, "Choose a video (.mp4, .mov, .webm…) or a picture (.png, .jpg)")
        with _lock:
            for old in plugin.data_dir.glob(f"{what}.*"):
                if old != part:
                    old.unlink()
            part.replace(final)
            s = settings()
            s["names"][what] = file.filename or final.name
            _save(s)
        return _status()

    @plugin.router.delete("/file/{what}")
    def remove(what: str):
        with _lock:
            for old in plugin.data_dir.glob(f"{what}.*"):
                old.unlink()
            s = settings()
            s["names"].pop(what, None)
            _save(s)
        return _status()

    @plugin.router.get("/file/{what}")
    def show(what: str):
        f = file_of(what) if what in ("logo", "intro", "outro") else None
        if not f:
            raise HTTPException(404)
        return FileResponse(f)
