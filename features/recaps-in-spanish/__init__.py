"""Recaps in Spanish, a Recap Studio feature plugin: a project's recap narrated in Spanish, Latin American or from
Spain. The script writer writes the narration in Spanish and the voice speaks it in Spanish; the subtitles and the
captions come from the narration, so they're in Spanish too, and so is the suggested YouTube title. The comic can be
in any language the app reads.

For a project set to Spanish (New project, or Project settings → Plugins → Language of the recap):
- the narration's writer and the title's are told to write in Spanish (plugin.script_instructions,
  plugin.title_instructions);
- the voice changes for that project only (plugin.settings_filter): Kokoro speaks with its Spanish voice (Dora or
  Alex), Microsoft Edge's with the region's (Dalia or Jorge for Latin America, Elvira or Álvaro for Spain), and
  ElevenLabs keeps your voice with its multilingual model;
- the captions time their words by the voice's pauses rather than the English speech recognizer.
Other projects don't change.
"""
import json

from fastapi import Body, HTTPException

VARIANTS = {  # id: (what it's called, Edge's female and male voices)
    "latam": ("Latin American Spanish", "es-MX-DaliaNeural", "es-MX-JorgeNeural"),
    "spain": ("Spanish from Spain", "es-ES-ElviraNeural", "es-ES-AlvaroNeural"),
}
KOKORO = {"female": "ef_dora", "male": "em_alex"}  # Kokoro's Spanish voices
ENGLISH_ONLY = {"eleven_monolingual_v1", "eleven_turbo_v2", "eleven_flash_v2"}  # ElevenLabs' English-only models
DEFAULTS = {"voice": "auto"}  # auto: female or male as the English voice chosen in Settings, else female

SCRIPT = """Language: write every narration line in {name}, as a Spanish-speaking recap channel narrates: natural, \
spoken Spanish, never a word-for-word translation. This replaces anything above about writing in English. Keep the \
characters' names as the character list spells them. Write numbers, symbols and abbreviations the way they're read \
aloud. Only the narration is in Spanish: everything else in your answer stays as asked."""

TITLE = """Language: write the title, the other titles and every word on the thumbnail (its labels and speech bubble) \
in {name}. Where a title would end in "| Manga Recap" (or Manhwa, Webtoon, Comic), end it the Spanish way: \
"| Resumen de manga" (de manhwa, de webtoon, del cómic)."""

_plugin = None
_key = ""
_saved = dict(DEFAULTS)  # (read once: the filter runs each time a project is loaded)


def _settings() -> dict:
    return _saved


def _load() -> None:
    try:
        _saved.update(json.loads((_plugin.data_dir / "settings.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass


def variant(settings: dict) -> str | None:
    v = settings.get(_key)
    return v if v in VARIANTS else None


def gender(settings: dict) -> str:
    """The Spanish voice's: as chosen, or as the English Kokoro voice is (af_, bf_: female; am_, bm_: male)."""
    chosen = _settings()["voice"]
    if chosen in ("female", "male"):
        return chosen
    voice = str(settings.get("voice") or "")
    return "male" if len(voice) > 1 and voice[1] == "m" else "female"


def overrides(settings: dict) -> dict:
    """What changes for a Spanish project: its voice, and captions timed without the English recognizer."""
    v = variant(settings)
    if not v:
        return {}
    g = gender(settings)
    out = {"voice": KOKORO[g], "edge_voice": VARIANTS[v][1 if g == "female" else 2], "captions_listen": False}
    if settings.get("elevenlabs_model") in ENGLISH_ONLY:
        out["elevenlabs_model"] = "eleven_multilingual_v2"
    return out


def setup(plugin):
    global _plugin, _key
    _plugin = plugin
    _load()
    _key = plugin.project_setting("language", "")  # "", "latam" or "spain"
    plugin.settings_filter(overrides)
    plugin.script_instructions(lambda s: SCRIPT.format(name=VARIANTS[variant(s)][0]) if variant(s) else "")
    plugin.title_instructions(lambda s: TITLE.format(name=VARIANTS[variant(s)][0]) if variant(s) else "")

    @plugin.router.get("/settings")
    def get_settings():
        return {**_settings(), "key": _key}

    @plugin.router.put("/settings")
    def put_settings(voice: str = Body(..., embed=True)):
        if voice not in ("auto", "female", "male"):
            raise HTTPException(400, "The voice is auto, female or male")
        (plugin.data_dir / "settings.json").write_text(json.dumps({"voice": voice}), encoding="utf-8")
        _saved["voice"] = voice
        return get_settings()
