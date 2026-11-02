// Branding, a Recap Studio feature plugin: its section in Settings (the logo, where it goes and how big, the intro and
// outro) and a choice in Project settings. The drawing and joining are __init__.py's.
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
    const fileRow = (what, label, accept, extra) => {
      const f = st.files[what];
      return el("div", { class: "key-row br-file" }, el("b", {}, label),
        f ? el("span", {}, f.name) : el("span", { class: "muted" }, "None"),
        el("button", { type: "button", class: "ghost small", onclick: () => pick(what, accept) }, f ? "Change…" : "Choose…"),
        f ? el("button", { type: "button", class: "ghost small", onclick: () => run(() => api(`${BASE}/file/${what}`,
          { method: "DELETE" })) }, "Remove") : null,
        f && f.picture && extra ? extra : null);
    };
    const seconds = (key) => el("label", {}, "shown for ", el("input", { type: "number", min: 1, max: 30, step: 0.5,
      value: s[key], class: "br-seconds", onchange: (e) => save({ [key]: e.target.value }) }), " seconds");
    const logo = st.files.logo;
    box.replaceChildren(...[  // (the rows left out are null)
      el("span", { class: "hint" }, "Your channel's logo in a corner of every video, and an intro and an outro before and " +
        "after each part. Each project can leave them out in its Project settings."),
      fileRow("logo", "Logo", "image/png,image/jpeg,image/webp"),
      logo ? el("div", { class: "br-logo" }, el("img", { src: `${BASE}/file/logo?t=${logo.stamp}`, alt: "Logo" })) : null,
      logo ? el("div", { class: "key-row" }, select("where", st.where, (v) => NAMES[v]), select("size", st.sizes, (v) => NAMES[v]),
        select("opacity", [1, 0.85, 0.7, 0.5], (v) => `${Math.round(v * 100)}% opaque`)) : null,
      logo ? el("span", { class: "hint" }, "A PNG with a see-through background looks best. It's drawn into each chapter as " +
        "it's rendered (the Shorts too), so a change renders the chapters again.") : null,
      fileRow("intro", "Intro", "video/*,image/*", seconds("intro_seconds")),
      fileRow("outro", "Outro", "video/*,image/*", seconds("outro_seconds")),
      el("span", { class: "hint" }, "A clip of up to 2 minutes with its sound (.mp4, .mov, .webm…), or a picture shown for some " +
        "seconds. It's fitted to each project's video, with black bars where the shapes differ, and added once the parts " +
        "are joined: the subtitles and YouTube chapters move along with it. Shorts and Export for editors don't get it."),
      error].filter(Boolean));
  }
})();
