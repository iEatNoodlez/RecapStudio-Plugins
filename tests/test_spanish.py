"""Recaps in Spanish and Reading Spanish comics, on a scratch data folder.

Recaps in Spanish: a project set to Spanish gets the Spanish voice for each provider (and only it), its captions timed
without the English recognizer, the narration's and the title's writers told to write Spanish; none of that is saved
into the project, and English projects don't change.
Reading Spanish comics: Spanish is a comic language with its own reader; Read text gets the reader ready first; what
the reader gets wrong on lettering in capitals (¡ read as "i") is put right; Remove (in its settings) deletes the
downloaded reader and leaves the plugin installed. With RECAP_TEST_MODELS=1 it also
downloads the reader (about 13 MB, into this test's own data folder, each file checked against its pinned checksum)
and reads pictures of Spanish text, accents and all, with no word turned upside down ("NO" read as "ON")."""
import json
import os
import time

from helpers import App, check, done, until

app = App(plugins=["recaps-in-spanish", "reading-spanish"], settings={"voice": "af_heart", "voice_provider": "kokoro"})
store = app.store
from recap import ocr, pipeline, script, translate, youtube  # noqa: E402

check("both load", app.loaded("recaps-in-spanish") and app.loaded("reading-spanish"))
KEY = "recaps-in-spanish.language"

# ---- Recaps in Spanish ----
check("its setting is a project setting, offered in New project", KEY in store.DEFAULT_SETTINGS and KEY in store.NEW_PROJECT_KEYS)
r = app.post("/api/projects", json={"name": "En español", "settings": {**store.new_project_settings(), KEY: "latam"}})
es = r.json()["id"]
check("New project starts from the last project's choice, as for its other choices",
      store.new_project_settings().get(KEY) == "latam")
english = app.project("In English", **{KEY: ""})
s = store.load(es)["settings"]
check("a Spanish project: Kokoro's Spanish voice, as the English one is female", s["voice"] == "ef_dora", s["voice"])
check("Edge's voice for Latin America", s["edge_voice"] == "es-MX-DaliaNeural", s["edge_voice"])
check("captions timed without the English recognizer", s["captions_listen"] is False)
e = store.load(english)["settings"]
check("an English project doesn't change", e["voice"] == "af_heart" and "captions_listen" not in e and not e.get(KEY))
store.update(es, lambda p: p["chapters"].append({"id": "c0001", "name": "Capítulo 1", "source": None}))
saved = json.loads((store.project_dir(es) / "project.json").read_text(encoding="utf-8"))["settings"]
check("none of it is saved into the project", saved.get(KEY) == "latam" and "captions_listen" not in saved
      and "voice" not in saved and "edge_voice" not in saved, saved)

prompt = script.system_prompt(store.load(es)["settings"], [])
check("the narration's writer is told: Latin American Spanish", "Latin American Spanish" in prompt
      and prompt.index("Latin American Spanish") < prompt.rindex("\n\n"), prompt[-600:])
check("not for an English project", "Spanish" not in script.system_prompt(store.load(english)["settings"], []))
title = "\n".join(script.extra_instructions(youtube.EXTRA, store.load(es)["settings"]))
check("the title's writer too, the Spanish way", "Resumen de manga" in title and "Latin American Spanish" in title, title)

r = app.put(f"/api/projects/{es}/settings", json={KEY: "spain"})
s = store.load(es)["settings"]
check("Project settings changes it: Spain", r.status_code == 200 and s["edge_voice"] == "es-ES-ElviraNeural"
      and "Spanish from Spain" in script.system_prompt(s, []), r.text)
app.put("/api/settings", json={"voice": "am_adam"})
check("a male English voice: the male Spanish one", store.load(es)["settings"]["voice"] == "em_alex"
      and store.load(es)["settings"]["edge_voice"] == "es-ES-AlvaroNeural")
r = app.put("/api/plugins/recaps-in-spanish/settings", json={"voice": "female"})
check("or the voice chosen in its settings", r.status_code == 200 and store.load(es)["settings"]["voice"] == "ef_dora")
check("a wrong choice refused", app.put("/api/plugins/recaps-in-spanish/settings", json={"voice": "robot"}).status_code == 400)
app.put("/api/settings", json={"voice_provider": "elevenlabs", "elevenlabs_model": "eleven_turbo_v2"})
check("ElevenLabs: its multilingual model", store.load(es)["settings"]["elevenlabs_model"] == "eleven_multilingual_v2"
      and store.load(english)["settings"]["elevenlabs_model"] == "eleven_turbo_v2")
app.put(f"/api/projects/{es}/settings", json={KEY: ""})
check("back to English: nothing changes any more", store.load(es)["settings"]["voice"] == "am_adam"
      and "Spanish" not in script.system_prompt(store.load(es)["settings"], []))

# ---- Reading Spanish comics ----
check("Spanish is a comic language", ocr.LANGUAGES.get("es") == "Spanish" and "accents" in translate.NAMES.get("es", ""))
raw = app.project("Cómic en español", language="es")
rs = store.load(raw)["settings"]
check("read with its own reader", ocr.language(rs) == "es" and ocr.model_for(rs) == "lang:es")
check("translated from Spanish", "Spanish" in translate.instructions(rs))
st = app.get("/api/plugins/reading-spanish/status").json()
check("its reader: a download of about 13 MB", 12 < st["download_mb"] < 14, st)
rs_mod = app.module("reading-spanish")


class FakeDownload:  # (the download itself isn't made here: it's the app's addon.Download, tested with the app)
    def __init__(self):
        self.started, self.calls = False, 0

    def installed(self):
        return self.started and self.calls > 2

    def start(self):
        self.started = True

    def status(self):
        self.calls += 1
        return {"installed": self.installed(), "state": "installing", "progress": 0.5, "message": "8 of 13 MB"}

    def cancel(self):
        pass


real, rs_mod.MODELS = rs_mod.MODELS, FakeDownload()
job = {"cancel": False, "message": "", "progress": 0}
start = time.time()
pipeline._reader_ready(job, "es")
check("Read text gets the reader ready first, saying so", rs_mod.MODELS.installed() and "Spanish text reader" in job["message"])
rs_mod.MODELS = real
fixes = {"iSI NO QUIERES TRABAJAR, RENUNCIA!": "¡SI NO QUIERES TRABAJAR, RENUNCIA!",  # (as read from a real webtoon)
         "icon LO OCUPADOS QUE ESTAMOS": "¡CON LO OCUPADOS QUE ESTAMOS", "AH, sí. ES QUE NO HICE": "AH, SÍ. ES QUE NO HICE",
         "OYE, iAH!": "OYE, ¡AH!", "Hmm...": "Hmm...", "¿Dónde está el señor Muñoz?": "¿Dónde está el señor Muñoz?"}
check("lettering in capitals: a small i starting a word is ¡, the rest capitals; other text as read",
      {t: rs_mod.fix(t) for t in fixes} == fixes, {t: rs_mod.fix(t) for t in fixes})
check("put right as the text is read (the language's fix)",
      [b["text"] for b in ocr.put_right([{"text": "iAH, sí. ES QUE NO!"}], "es")] == ["¡AH, SÍ. ES QUE NO!"])

# Remove, in its settings: the downloaded reader goes, the plugin stays (DELETE /api/plugins/reading-spanish itself is
# the app's, which removes the plugin)
folder = app.data / "plugins" / "reading-spanish"
state = folder.parent / "features.json"
before = (sorted(p.name for p in folder.rglob("*")), state.read_bytes() if state.exists() else None, app.plugins.changed)
if not real.folder.is_relative_to(app.data):
    raise SystemExit(f"the reader's folder isn't in the test's data folder: {real.folder}")
real.folder.mkdir(parents=True, exist_ok=True)
for name in [*real.files, "rec_keys.txt", "installed.json"]:  # (a stand-in reader, as if downloaded)
    real.path(name).write_text("stand-in", encoding="utf-8")
check("(a stand-in reader, as if downloaded)", real.installed())
r = app.delete("/api/plugins/reading-spanish/models")
check("Remove deletes the downloaded reader", r.status_code == 200 and not r.json()["installed"]
      and not real.folder.exists(), r.text)
after = (sorted(p.name for p in folder.rglob("*")), state.read_bytes() if state.exists() else None, app.plugins.changed)
check("and leaves the plugin installed: its folder and features.json untouched, no restart asked for",
      before == after and (folder / "plugin.json").exists() and app.loaded("reading-spanish")
      and app.get("/api/plugins/reading-spanish/status").json()["installed"] is False, (before, after))

if os.environ.get("RECAP_TEST_MODELS"):  # (downloads the reader, about 13 MB, into this test's own data folder)
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    job = {"cancel": False, "message": "", "progress": 0}
    pipeline._reader_ready(job, "es")
    check("downloaded, each file checked against its pinned checksum", real.installed(), real.status())

    def read(words, font="arial.ttf"):
        img = Image.new("RGB", (1100, 160), "white")
        ImageDraw.Draw(img).text((20, 40), words, fill="black", font=ImageFont.truetype(font, 56))
        bubbles = ocr.read_bubbles(cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR), "lang:es", " ", False, use_gpu=False)
        return " ".join(b["text"] for b in ocr.put_right(bubbles, "es"))
    text = read("OYE, NO PUEDE SER!", "arialbd.ttf")  # (with RapidOCR's angle check: "OYE, ON PUEDE SER!")
    check("no word turned upside down: NO isn't read as ON", "NO PUEDE SER" in text, text)
    text = read("¿Dónde está el señor Muñoz?")
    check("reads Spanish, accents and all", "¿Dónde está el señor Muñoz?" in text, text)
    text = read("¿DÓNDE ESTÁ EL SEÑOR MUÑOZ?")
    check("in capitals too, as comics are lettered", "DÓNDE" in text and "MUÑOZ" in text and "ESTÁ" in text, text)
    print(f"(¡Ay, no! is read as {read('¡Ay, no!')!r}: the reader's alphabet has no ¡)")
else:
    print("(reading Spanish is tested with RECAP_TEST_MODELS=1, which downloads the reader, about 13 MB)")
done()
