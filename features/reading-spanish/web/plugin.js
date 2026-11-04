// Reading Spanish comics, a Recap Studio feature plugin: Spanish in the comic-language lists (New project, Project
// settings), and the Spanish text reader in Settings. How it's read is __init__.py's.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el } = R;
  const BASE = "/api/plugins/reading-spanish";
  R.addLanguage("es", "Spanish");

  R.addSettings("Reading Spanish comics", async (box) => {
    let st;
    try { st = await api(`${BASE}/status`); } catch (e) { box.replaceChildren(el("span", { class: "error" }, e.message)); return; }
    const act = (method, path) => async () => {
      try { st = await api(`${BASE}${path}`, { method }); } catch (e) { return R.notify(e.message); }
      draw();
      if (st.state === "installing") setTimeout(function poll() {
        api(`${BASE}/status`).then((s) => { st = s; draw(); if (s.state === "installing") setTimeout(poll, 1000); });
      }, 1000);
    };
    const draw = () => box.replaceChildren(
      el("span", { class: "hint" }, "A comic in Spanish is read in Spanish and translated into English by your script writer, " +
        "as Korean, Chinese and Japanese ones are: choose Spanish as the comic's language (New project, or Project settings)."),
      el("span", { class: "hint" }, st.installed ? "Spanish text reader: downloaded."
        : st.state === "installing" ? `Downloading the Spanish text reader: ${st.message}`
        : st.state === "error" ? `The download failed: ${st.error}`
        : `Spanish text reader: not downloaded yet (${st.download_mb} MB, PaddleOCR's PP-OCRv5, Apache 2.0). Read text downloads it the first time.`),
      el("div", { class: "key-row" }, st.installed
        ? el("button", { type: "button", class: "ghost small danger", onclick: act("DELETE", "/models") }, "Remove")
        : st.state === "installing" ? el("button", { type: "button", class: "ghost small", onclick: act("POST", "/cancel") }, "Cancel")
        : el("button", { type: "button", class: "ghost small", onclick: act("POST", "/download") }, "Download now")));
    draw();
  });
})();
