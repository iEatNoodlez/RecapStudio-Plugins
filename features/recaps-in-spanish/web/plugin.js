// Recaps in Spanish, a Recap Studio feature plugin: the recap's language in New project and Project settings, and
// which Spanish voice in Settings. What it changes is __init__.py's.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el } = R;
  const KEY = "recaps-in-spanish.language";
  const BASE = "/api/plugins/recaps-in-spanish";
  const OPTIONS = [["", "English"], ["latam", "Spanish (Latin America)"], ["spain", "Spanish (Spain)"]];
  const HOW = "The narration, the voice, the subtitles, the captions and the suggested YouTube title. The voice: Kokoro's " +
    "Spanish one, Microsoft Edge's for the region, or your ElevenLabs voice speaking Spanish.";
  const SAFE = "YouTube-safe's check of the narration's wording only reads English: read a Spanish script yourself.";

  R.addProjectSetting({ key: KEY, label: "Language of the recap", options: OPTIONS, hint: HOW });

  // New project: under the comic's own language
  R.on("new-project-page", ({ page, body, state }) => {
    if (page !== "comic") return;
    const s = state.settings;
    const pick = el("select", { onchange: (e) => { s[KEY] = e.target.value; state.redraw(); } },
      ...OPTIONS.map(([v, t]) => el("option", { value: v }, t)));
    pick.value = s[KEY] || "";
    body.append(...[el("label", {}, "Language of the recap ", pick),
      s[KEY] ? el("span", { class: "hint" }, HOW) : null,
      s[KEY] && s.youtube_safe ? el("span", { class: "hint warn" }, SAFE) : null].filter(Boolean));
  });

  // Settings → Recaps in Spanish: which voice
  R.addSettings("Recaps in Spanish", async (box) => {
    let st;
    try { st = await api(`${BASE}/settings`); } catch (e) { box.replaceChildren(el("span", { class: "error" }, e.message)); return; }
    const voice = el("select", { onchange: async (e) => {
      try { await api(`${BASE}/settings`, { method: "PUT", json: { voice: e.target.value } }); } catch (err) { R.notify(err.message); }
    } }, el("option", { value: "auto" }, "As the English voice (female or male)"),
    el("option", { value: "female" }, "Female: Dora, Dalia or Elvira"), el("option", { value: "male" }, "Male: Alex, Jorge or Álvaro"));
    voice.value = st.voice;
    box.replaceChildren(
      el("span", { class: "hint" }, "For projects whose recap is in Spanish (New project, or Project settings → Plugins). " + HOW),
      el("label", {}, "Spanish voice ", voice),
      el("span", { class: "hint" }, SAFE));
  });
})();
