"""Reading Spanish comics, a Recap Studio feature plugin: a comic in Spanish (a raw) is read in Spanish, then the
script writer translates it into English (the Translate step) before the narration is written, as with Korean,
Chinese and Japanese. The English can be lettered into the pictures too, as for them.

Its text is read with PaddleOCR's PP-OCRv5 models (Apache 2.0, from PaddlePaddle's own repositories): the text finder
the Korean, Chinese and Japanese reader uses, and the recognizer for Latin-alphabet languages, which reads Spanish
with its accents, ñ and ¿ (its alphabet has no ¡). They're downloaded the first time a Spanish project's text is
read (about 13 MB, into data\\addons\\reading-spanish, pinned and checked against their checksums), never part of the
app or of the plugin.
"""
import time

from fastapi import HTTPException

from recap import addon, pipeline, store

REPO = "PaddlePaddle/latin_PP-OCRv5_mobile_rec_onnx"
REV = "89d3a50e2c27e2e7cceeab0e944c25c807d5db4f"
MODELS = addon.Download(store.ADDONS_DIR / "reading-spanish", {
    "det.onnx": ("PaddlePaddle/PP-OCRv5_mobile_det_onnx", "e6f4fa85f00e168c862bc462aebca69eef9b3d3d",
                 "inference.onnx", 4_826_518, "a431985659dc921974177a95adcfbb90fd9e51989a5e04d70d0b75f597b6e61d"),
    "rec.onnx": (REPO, REV, "inference.onnx", 8_042_023,
                 "7888113072263cb471b93f66dd5e2ad70548dc526fa1ace760d0d973dd121498"),
    "rec.yml": (REPO, REV, "inference.yml", 6_817, "0bbe984570f597af3638e50bdf2e8276f3ab26a61966096538b3b0d1849f5c84"),
})
NAMES = "as the comic writes them, with their accents (José Muñoz)"


def _keys():
    """The recognizer's characters, one a line, written from its inference.yml the first time."""
    out = MODELS.path("rec_keys.txt")
    if not out.exists():
        import yaml
        info = yaml.safe_load(MODELS.path("rec.yml").read_text(encoding="utf-8"))
        out.write_text("\n".join(str(c) for c in info["PostProcess"]["character_dict"]) + "\n", encoding="utf-8")
    return out


def reader() -> dict:
    """RapidOCR's arguments for these models (ocr.READERS)."""
    if not MODELS.installed():
        raise RuntimeError("The Spanish text reader isn't downloaded yet")
    return {"det_model_path": str(MODELS.path("det.onnx")), "rec_model_path": str(MODELS.path("rec.onnx")),
            "rec_keys_path": str(_keys())}


def ready(job) -> None:
    """Before a Spanish project's text is read: the models, downloaded first when they aren't yet."""
    if MODELS.installed():
        return
    try:
        MODELS.start()
    except ValueError:
        pass  # (already downloading)
    while True:
        st = MODELS.status()
        if st["installed"]:
            return
        if st["state"] == "error":
            raise RuntimeError(f"The Spanish text reader didn't download: {st['error']}")
        if st["state"] != "installing":
            raise RuntimeError("The Spanish text reader wasn't downloaded")
        if job["cancel"]:
            MODELS.cancel()
            raise pipeline.Cancelled()
        job["message"] = f"Downloading the Spanish text reader: {st['message']}"
        job["progress"] = st["progress"]
        time.sleep(0.5)


def setup(plugin):
    plugin.language("es", "Spanish", names=NAMES, reader=reader, ready=ready)

    @plugin.router.get("/status")
    def status():
        return MODELS.status()

    @plugin.router.post("/download")
    def download():
        try:
            return MODELS.start()
        except ValueError as e:
            raise HTTPException(409, str(e))

    @plugin.router.post("/cancel")
    def cancel():
        MODELS.cancel()
        return MODELS.status()

    @plugin.router.delete("")
    def remove():
        try:
            return MODELS.remove()
        except ValueError as e:
            raise HTTPException(409, str(e))
