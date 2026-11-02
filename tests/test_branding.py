"""Branding, with made-up projects rendered for real: the logo over the frames where it's set, an intro clip (4:3, with
a tone) and an outro picture added to each part of the video, fitted with black bars, joined without errors, with the
sound in its place, the subtitles and the YouTube chapter list moved along and the length saved; a project with the
logo only, and one with nothing; files that can't be an intro refused; and taking one away."""
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
import soundfile as sf

from helpers import App, check, done, scratch


def main():  # (the video is drawn in several processes: they import this file, and mustn't run it)
    global app, pipeline, plugins, store, video, FFMPEG, br

    app = App(plugins=["branding"])
    pipeline, plugins, store = app.pipeline, app.plugins, app.store
    from recap import video  # noqa: E402

    FFMPEG = video.find_ffmpeg()
    check("plugin loaded", app.loaded("branding"), plugins.loaded.get("branding"))
    br = app.module("branding")
    VIDEO = {"resolution": 360, "fps": 10, "video_style": "stills", "orientation": "landscape", "transition_seconds": 0.0,
             "layout": "pages", "shorts": 0, "captions": False, "background_music": False, "video_split": "none"}


    def made_up(name: str, use: str, **settings) -> str:
        """A project of two chapters, each two panels narrated by a tone (a second each, shown for two: 4 s a chapter)."""
        pid = app.project(name, **{**VIDEO, **settings, "branding.use": use})
        d = store.project_dir(pid)
        for sub in ("pages", "panels", "audio"):
            (d / sub).mkdir(parents=True, exist_ok=True)

        def fill(p):
            for c in (1, 2):
                cid = f"c{c:04d}"
                p["chapters"].append({"id": cid, "name": f"Chapter {c}", "source": None})
                cv2.imwrite(str(d / "pages" / f"{cid}_0000.png"), np.full((900, 600, 3), 240, np.uint8))
                p["pages"].append({"file": f"{cid}_0000.png", "name": "001.png", "w": 600, "h": 900, "chapter": cid})
                for k in range(2):
                    xid = f"{cid}_{k:05d}"
                    cv2.imwrite(str(d / "panels" / f"{xid}.png"), np.full((400, 600, 3), (200, 200, 200), np.uint8))
                    t = np.arange(24000) / 24000
                    sf.write(d / "audio" / f"{xid}.wav", (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32), 24000)
                    p["panels"].append({"id": xid, "chapter": cid, "page": 0, "file": f"{xid}.png", "joined": False,
                                        "text": "Line.", "narration": f"Panel {k} of chapter {c}.", "audio": f"{xid}.wav",
                                        "duration": 1.0, "enabled": True, "box": [0, 0, 600, 400], "ocr_done": True,
                                        "script_done": True})
        store.update(pid, fill)
        return pid


    def render(pid) -> dict:
        app.post(f"/api/projects/{pid}/run", json={"steps": ["video"]})
        end = __import__("time").time() + 300
        while __import__("time").time() < end:
            job = pipeline.latest_for(pid)
            if job and job["status"] not in pipeline.ACTIVE:
                return job
            __import__("time").sleep(0.3)
        return {}


    def frame(path: Path, at: float):
        cap = cv2.VideoCapture(str(path))
        cap.set(cv2.CAP_PROP_POS_MSEC, at * 1000)
        ok, img = cap.read()
        cap.release()
        return img if ok else np.zeros((360, 640, 3), np.uint8)


    def loud(path: Path, a: float, b: float) -> float:
        """How loud the video's sound is between two times (RMS)."""
        wav = work / "sound.wav"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "24000", str(wav)],
                       check=True)
        s, rate = sf.read(wav)
        part = s[int(a * rate):int(b * rate)]
        return float(np.sqrt(np.mean(part ** 2))) if len(part) else 0.0


    def clean(path: Path) -> bool:
        r = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
        return r.returncode == 0 and not r.stderr.strip()


    # ---- the files ----
    work = scratch()
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=640x480:r=25:d=2", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=2", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(work / "My intro.mp4")], check=True)
    cv2.imwrite(str(work / "outro.png"), np.full((720, 1280, 3), (255, 0, 0), np.uint8))  # blue
    logo = np.zeros((60, 60, 4), np.uint8)
    logo[:, :, 1] = logo[:, :, 3] = 255  # green, opaque
    cv2.imwrite(str(work / "logo.png"), logo)
    (work / "notes.txt").write_text("not a video", encoding="utf-8")


    def upload(what, path, name=None):
        with open(path, "rb") as f:
            return app.post(f"/api/plugins/branding/file/{what}", files={"file": (name or path.name, f)})


    check("a file that's no video or picture refused", upload("intro", work / "notes.txt").status_code == 400)
    check("a 'video' that isn't one refused", upload("intro", work / "notes.txt", "fake.mp4").status_code == 400)
    check("the logo taken", upload("logo", work / "logo.png").ok)
    check("an intro clip taken", upload("intro", work / "My intro.mp4").ok)
    r = upload("outro", work / "outro.png")
    check("an outro picture taken", r.ok and r.json()["files"]["outro"]["picture"], r.text)
    r = app.put("/api/plugins/branding/settings", json={"where": "top-left", "size": "small", "opacity": 1, "outro_seconds": 1.5,
                                                        "intro_seconds": 99, "nonsense": 1})
    check("settings saved, kept in bounds", r.ok and r.json()["settings"]["where"] == "top-left"
          and r.json()["settings"]["intro_seconds"] == 30 and r.json()["files"]["intro"]["name"] == "My intro.mp4", r.text)

    # ---- a project with all of it, in two parts ----
    both = made_up("All of it", "all", video_split="chapters", video_split_chapters=1)
    job = render(both)
    check("rendered", job.get("status") == "done", job)
    check("the job says so", "With the intro and outro (Branding)" in (job.get("message") or ""), job.get("message"))
    check("the recap's picture copied, not encoded again", "encoded again" not in job.get("message", ""))
    p = store.load(both)
    d = store.project_dir(both)
    parts = [d / x["video"] for x in p["output"]["parts"]]
    check("two parts", len(parts) == 2, parts)
    for n, part in enumerate(parts, 1):
        seconds = br.probe(part)["seconds"]
        saved = p["output"]["parts"][n - 1]["seconds"]
        check(f"part {n}: 2 s intro + its chapter (4 s) + 1.5 s outro, the length saved", abs(seconds - saved) < 0.2
              and abs(saved - (2 + 4 + 1.5)) < 0.3, (seconds, saved))
        first = frame(part, 1.0)
        check(f"part {n}: the intro first, fitted with black bars", first[180, 320, 2] > 200 and first[180, 320, 1] < 60
              and first[180, 20].max() < 30, (first[180, 320], first[180, 20]))
        mid = frame(part, 3.0)
        check(f"part {n}: then the chapter, with the logo top left", mid[20, 20, 1] > 200 and mid[20, 20, 2] < 60
              and mid[180, 320].min() > 150, (mid[20, 20], mid[180, 320]))
        last = frame(part, seconds - 0.4)
        check(f"part {n}: the outro picture last", last[180, 320, 0] > 200 and last[180, 320, 2] < 60, last[180, 320])
        check(f"part {n}: joined without a decoding error", clean(part))
        check(f"part {n}: the intro's tone, then the narration, then quiet", loud(part, 0.3, 1.7) > 0.05
              and loud(part, 2.3, 3.7) > 0.05 and loud(part, seconds - 1.2, seconds - 0.2) < 0.01,
              (loud(part, 0.3, 1.7), loud(part, 2.3, 3.7), loud(part, seconds - 1.2, seconds - 0.2)))
    srt = (d / p["output"]["parts"][0]["srt"]).read_text(encoding="utf-8")
    check("the subtitles moved along by the intro", srt.split("\n")[1].startswith("00:00:02,000 --> 00:00:03,000"), srt[:80])
    whole = made_up("Whole", "all")
    render(whole)
    pw = store.load(whole)
    marks = (store.project_dir(whole) / pw["output"]["parts"][0]["chapters"]).read_text(encoding="utf-8").splitlines()
    check("the chapter list: the first at 0:00, the next moved along", marks == ["0:00 Chapter 1", "0:06 Chapter 2"], marks)
    check("... and the length", abs(pw["output"]["parts"][0]["seconds"] - 11.5) < 0.3, pw["output"]["parts"][0]["seconds"])

    # ---- the logo only, and nothing ----
    only = made_up("Logo only", "logo")
    render(only)
    po = store.load(only)
    out = store.project_dir(only) / po["output"]["video"]
    check("logo only: no intro, the logo there", abs(br.probe(out)["seconds"] - 8) < 0.3 and frame(out, 0.5)[20, 20, 1] > 200,
          (br.probe(out)["seconds"], frame(out, 0.5)[20, 20]))
    none = made_up("Nothing", "none")
    render(none)
    pn = store.load(none)
    out = store.project_dir(none) / pn["output"]["video"]
    corner = frame(out, 0.5)[20, 20].astype(int)  # (the background around the panel, not green)
    check("nothing: no intro, no logo", abs(br.probe(out)["seconds"] - 8) < 0.3 and corner[1] - corner[2] < 40,
          (br.probe(out)["seconds"], corner))
    check("the logo is in the chapters' fingerprint (changing it renders them again)",
          json.dumps(br.overlay({"branding.use": "all"})) != json.dumps(br.overlay({"branding.use": "none"})))

    # ---- the CPU's encoder, and an intro made with another encoder than the recap's ----
    cpu = made_up("On the CPU", "all")
    app.put("/api/settings", json={"video_encoder": "cpu"})  # (an app-wide setting)
    job = render(cpu)
    app.put("/api/settings", json={"video_encoder": "auto"})
    out = store.project_dir(cpu) / store.load(cpu)["output"]["video"]
    check("made on the CPU: found from the video, copied, clean", "encoded again" not in job.get("message", "")
          and br.encoder_of(out, store.load(cpu)["settings"]) == video.CPU_ENCODER and clean(out), job.get("message"))
    other = made_up("Mixed", "all")
    real = br.encoder_of
    br.encoder_of = lambda main, s: (["-c:v", "libx264", "-preset", "ultrafast", "-profile:v", "baseline", "-crf", "30"])
    job = render(other)
    br.encoder_of = real
    out = store.project_dir(other) / store.load(other)["output"]["video"]
    check("an intro from another encoder: still joined cleanly (encoded again only if it had to be)",
          job.get("status") == "done" and clean(out) and abs(br.probe(out)["seconds"] - 11.5) < 0.3, job.get("message"))
    print("  (mixed encoders:", "encoded again" if "encoded again" in job.get("message", "") else "copied", ")")

    # ---- taking one away ----
    r = app.delete("/api/plugins/branding/file/intro")
    check("the intro taken away", r.ok and r.json()["files"]["intro"] is None and br.file_of("intro") is None)
    render(whole)
    pw = store.load(whole)
    check("then only the outro is added", abs(pw["output"]["parts"][0]["seconds"] - 9.5) < 0.3, pw["output"]["parts"][0]["seconds"])
    done()


if __name__ == "__main__":
    main()
