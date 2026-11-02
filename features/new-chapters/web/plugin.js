// New chapters on a schedule, a Recap Studio feature plugin: its section in Settings (the days and the time, what
// each project made from a source does with its new chapters, and what it has done), and the same choice in each
// project's Project settings. The checking is __init__.py's.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el } = R;
  const BASE = "/api/plugins/new-chapters";
  const KEY = "new-chapters.when_new";
  const CHOICES = [["off", "Don't check"], ["tell", "Check, and tell me"], ["get", "Get them"],
    ["recap", "Get them, and recap just them"], ["all", "Get them, and make the whole recap"]];

  R.addProjectSetting({ key: KEY, label: "New chapters on a schedule:", options: CHOICES,
    hint: "For a project made from a site (Get chapters): what to do with its new chapters at the times set in Settings. " +
      "\"Recap just them\" makes a video of the new chapters alone." });

  R.addSettings("New chapters on a schedule", async (box) => {
    let st;
    try { st = await api(`${BASE}/status`); } catch (e) { box.replaceChildren(el("span", { class: "error" }, e.message)); return; }
    draw(box, st);
  });

  function draw(box, st) {
    const s = st.settings;
    const error = el("div", { class: "error" });
    const save = async (change) => {
      try { draw(box, await api(`${BASE}/settings`, { method: "PUT", json: { days: s.days, at: s.time, ...change } })); }
      catch (e) { error.textContent = e.message; }
    };
    const days = el("div", { class: "nc-days" }, ...st.days.map((d, i) => el("label", { class: "check" },
      el("input", { type: "checkbox", checked: s.days.includes(i), onchange: (e) => save({
        days: e.target.checked ? [...s.days, i] : s.days.filter((x) => x !== i) }) }), ` ${d}`)));
    const at = el("input", { type: "time", value: s.time, onchange: (e) => e.target.value && save({ at: e.target.value }) });
    const rows = st.projects.map((p) => {
      const pick = el("select", { onchange: async (e) => {
        try { await api(`/api/projects/${p.id}/settings`, { method: "PUT", json: { [KEY]: e.target.value } }); }
        catch (err) { error.textContent = err.message; }
      } }, ...CHOICES.map(([v, t]) => el("option", { value: v, selected: v === p.choice }, t)));
      const extra = [p.new ? `${p.new} new` : "", p.doing].filter(Boolean).join("; ");
      return el("div", { class: "nc-project" }, el("b", {}, p.name), pick,
        extra ? el("span", { class: "muted" }, extra) : null);
    });
    box.replaceChildren(
      el("span", { class: "hint" }, "Projects made from a site (Get chapters) are checked for new chapters through the " +
        "source their chapters came from: your own plugin, gallery-dl or Suwayomi. This plugin gets nothing by itself."),
      el("div", { class: "nc-when" }, el("span", {}, "Check on"), days),
      el("div", { class: "nc-when" }, el("span", {}, "At"), at),
      el("div", { class: "hint" }, !s.days.length ? "Off: no days ticked."
        : `Next check: ${st.next}. Recap Studio has to be open then; if it isn't, it checks when it next opens.` +
          (st.last ? ` Last checked ${st.last}.` : "")),
      ...(rows.length ? [el("div", { class: "nc-projects" }, ...rows)]
        : [el("div", { class: "hint" }, "No project was made from a site yet: Get chapters from a site makes one.")]),
      error,
      ...(st.log.length ? [el("div", { class: "nc-log" }, ...st.log.slice(0, 8).map((x) =>
        el("div", {}, el("span", { class: "muted" }, x.at), " ", x.project ? el("b", {}, x.project) : null,
          x.project ? ": " : "", x.note)))] : []),
      el("div", { class: "key-row" }, el("button", { type: "button", class: "ghost small", disabled: st.checking,
        onclick: async (e) => {
          e.target.disabled = true;
          e.target.textContent = "Checking…";
          try { await api(`${BASE}/check`, { method: "POST" }); } catch (err) { error.textContent = err.message; }
          const wait = async () => {
            const now = await api(`${BASE}/status`).catch(() => null);
            if (now && now.checking) return setTimeout(wait, 1500);
            if (now) draw(box, now);
          };
          wait();
        } }, st.checking ? "Checking…" : "Check now")));
  }
})();
