// Branding, a Recap Studio feature plugin: its section in Settings (a card each for the logo, the intro and the outro:
// the file, where the logo goes and how big, how long a picture shows) and a choice in Project settings. The drawing
// and joining are __init__.py's.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el } = R;
  const BASE = "/api/plugins/branding";
  const NAMES = { "top-left": "Top left", "top-right": "Top right", "bottom-left": "Bottom left", "bottom-right": "Bottom right",
    small: "Small", medium: "Medium", large: "Large" };

  R.addProjectSetting({ key: "branding.use", label: "Branding:", options: [["all", "Logo, intro and outro"],
    ["logo", "Logo only"], ["clips", "Intro and outro only"], ["none", "None"]],
  hint: "As set in Settings → Branding. The logo is drawn into the chapters, so changing it renders them again." });

  R.addSettings("Branding", async (box) => {
    let st;
    try { st = await api(`${BASE}/status`); } catch (e) { box.replaceChildren(el("span", { class: "error" }, e.message)); return; }
    draw(box, st);
  });

  function draw(box, st) {
    const s = st.settings;
    const error = el("div", { class: "error" });
    const run = async (call) => { try { draw(box, await call()); } catch (e) { error.textContent = e.message; } };
    const save = (change) => run(() => api(`${BASE}/settings`, { method: "PUT", json: change }));
    const pick = (what, accept) => {
      const input = el("input", { type: "file", accept, hidden: true, onchange: () => {
        const f = input.files[0];
        if (!f) return;
        const fd = new FormData();
        fd.append("file", f, f.name);
        error.textContent = "Adding it…";
        run(() => api(`${BASE}/file/${what}`, { method: "POST", body: fd }));
      } });
      document.body.append(input);
      input.click();
      setTimeout(() => input.remove(), 60000);
    };
    const select = (key, values, text) => el("select", { onchange: (e) => save({ [key]: e.target.value }) },
      ...values.map((v) => el("option", { value: v, selected: String(v) === String(s[key]) }, text(v))));
    const field = (label, control) => el("label", { class: "pk-field" }, label, control);
    const chip = (f) => el("span", { class: `side-chip${f ? " ok" : ""}` }, f ? "set" : "none");
    // the file's name (or none), with Choose… / Change… and Remove
    const fileRow = (what, accept, f) => el("div", { class: "pk-actions" },
      f ? el("span", {}, f.name) : el("span", { class: "muted" }, "None yet"),
      el("button", { type: "button", class: "ghost small", onclick: () => pick(what, accept) }, f ? "Change…" : "Choose…"),
      f ? el("button", { type: "button", class: "ghost small", onclick: () => run(() => api(`${BASE}/file/${what}`,
        { method: "DELETE" })) }, "Remove") : null);
    const seconds = (key) => el("label", { class: "pk-line" }, "Shown for", el("input", { type: "number", min: 1, max: 30, step: 0.5,
      value: s[key], class: "pk-num", onchange: (e) => save({ [key]: e.target.value }) }), el("span", { class: "muted" }, "seconds"));
    const clip = (what, title, f, key) => el("section", { class: "pk-card" }, el("h3", {}, title, chip(f)),
      fileRow(what, "video/*,image/*", f), f && f.picture ? seconds(key) : null);
    const { logo, intro, outro } = st.files;
    box.replaceChildren(...[  // (the rows left out are null)
      el("span", { class: "hint" }, "Your channel's logo in a corner of every video, and an intro and an outro before and " +
        "after each part. Each project can leave them out in its own settings (the panel beside the stages)."),
      el("section", { class: "pk-card" }, el("h3", {}, "Logo", chip(logo)),
        el("div", { class: "pk-row" },
          logo ? el("span", { class: "pk-preview" }, el("img", { src: `${BASE}/file/logo?t=${logo.stamp}`, alt: "Logo" })) : null,
          fileRow("logo", "image/png,image/jpeg,image/webp", logo)),
        logo ? el("div", { class: "pk-fields" }, field("Corner", select("where", st.where, (v) => NAMES[v])),
          field("Size", select("size", st.sizes, (v) => NAMES[v])),
          field("Opacity", select("opacity", [1, 0.85, 0.7, 0.5], (v) => `${Math.round(v * 100)}%`))) : null,
        el("span", { class: "hint" }, "A PNG with a see-through background looks best. It's drawn into each chapter as " +
          "it's rendered (the Shorts too), so a change renders the chapters again.")),
      el("div", { class: "pk-cards" }, clip("intro", "Intro", intro, "intro_seconds"), clip("outro", "Outro", outro, "outro_seconds")),
      el("span", { class: "hint" }, "A clip of up to 2 minutes with its sound (.mp4, .mov, .webm…), or a picture shown for some " +
        "seconds. It's fitted to each project's video, with black bars where the shapes differ, and added once the parts " +
        "are joined: the subtitles and YouTube chapters move along with it. Shorts and Export for editors don't get it."),
      error].filter(Boolean));
  }
})();
