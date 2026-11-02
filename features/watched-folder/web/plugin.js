// Watched folder, a Recap Studio feature plugin: its section in Settings (the folder, what to do with what's
// dropped in, and what it has done). The looking is __init__.py's, every 30 seconds while the app is open.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el } = R;
  const BASE = "/api/plugins/watched-folder";

  R.addSettings("Watched folder", async (box) => {
    let st;
    try { st = await api(`${BASE}/status`); } catch (e) { box.replaceChildren(el("span", { class: "error" }, e.message)); return; }
    draw(box, st);
  });

  function draw(box, st) {
    const s = st.settings;
    const save = async (change) => {
      try { draw(box, await api(`${BASE}/settings`, { method: "PUT", json: { ...s, ...change } })); }
      catch (e) { error.textContent = e.message; }
    };
    const error = el("div", { class: "error" }, st.error && s.folder ? st.error : "");
    const path = el("input", { value: s.folder, placeholder: "A folder of its own, e.g. D:\\Comics to recap", spellcheck: "false",
      onchange: (e) => save({ folder: e.target.value }) });
    const tick = (key, text) => el("label", { class: "check" },
      el("input", { type: "checkbox", checked: !!s[key], onchange: (e) => save({ [key]: e.target.checked }) }), ` ${text}`);
    box.replaceChildren(...[  // (the rows left out are null)
      el("span", { class: "hint" }, "Put a folder named after a project in it, and drop that project's chapters there: " +
        "folders of pictures, .zip or .cbz. They're added once they've finished copying, numbered like Add chapters " +
        "does it. Nothing is moved or deleted, and chapters made of pictures are read from there, so leave them."),
      el("span", { class: "hint wf-example" }, "For example: …\\Give My Regards to Black Jack\\Chapter 12.cbz"),
      el("div", { class: "key-row" }, path,
        el("button", { type: "button", class: "ghost small", onclick: async () => {
          const picked = await R.pickFolder(s.folder);
          if (picked) save({ folder: picked });
        } }, "Choose…")),
      tick("recap", "Make the recap for the chapters it adds"),
      tick("create", "A folder named after no project makes a new one (with the last project's choices)"),
      error,
      el("span", { class: "hint" }, !s.folder ? "Off until a folder is chosen."
        : `Looks every 30 seconds while Recap Studio is open${st.last ? `; last looked ${st.last}` : ""}.` +
          (st.waiting ? ` ${st.waiting} chapter${st.waiting === 1 ? "" : "s"} still copying.` : "")),
      ...(st.log.length ? [el("div", { class: "wf-log" }, ...st.log.slice(0, 8).map((x) =>
        el("div", {}, el("span", { class: "muted" }, x.at), " ", el("b", {}, x.project), ": ", x.note)))] : []),
      s.folder ? el("div", { class: "key-row" }, el("button", { type: "button", class: "ghost small", onclick: async () => {
        try { draw(box, await api(`${BASE}/look`, { method: "POST" })); } catch (e) { error.textContent = e.message; }
      } }, "Look now")) : null].filter(Boolean));
  }
})();
