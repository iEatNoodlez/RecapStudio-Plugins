// Overnight queue, a Recap Studio feature plugin: a sidebar button opens the queue. Tick the projects, put them in
// order, choose when it starts and stops, and Make the recap runs them one after another (__init__.py).
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el, plural } = R;
  const BASE = "/api/plugins/overnight-queue";
  const STATUS = { waiting: "Waiting", running: "Running", done: "Done", left: "Left for you", failed: "Failed",
                   cancelled: "Cancelled", paused: "Paused", "left running": "Left running" };

  let q = { state: "idle", items: [] }, projects = [], picked = [], timer = null;
  let form = { start: "", stop: "", useStart: false, useStop: false, onAsk: "skip" };
  let editing = false;  // making a new queue while the last one's results are still there

  const button = R.addButton("sidebar", "Overnight queue…", open, { title: "Make the recap for several projects, one after another" });
  const dlg = el("dialog", { class: "wide-dialog oq-dialog" });
  const body = el("div", { class: "setup-body" });
  const actions = el("div", { class: "import-actions" });
  dlg.append(el("form", { method: "dialog", class: "import-form" },
    el("div", { class: "setup-head" }, el("h2", {}, "Overnight queue")), body, actions));
  document.body.append(dlg);

  function label() {
    const done = q.items.filter((it) => !["waiting", "running"].includes(it.status)).length;
    button.textContent = q.state === "waiting" ? `Queue starts at ${q.start_clock}`
      : q.state === "running" ? `Queue: ${Math.min(done + 1, q.items.length)} of ${q.items.length}`
      : q.state === "done" ? "Queue done ✓" : q.state === "stopped" ? "Queue stopped" : "Overnight queue…";
  }

  async function refresh() {
    try { q = await api(`${BASE}/state`); } catch { return; }
    label();
    if (dlg.open) draw();
    clearTimeout(timer);
    if (["waiting", "running"].includes(q.state)) timer = setTimeout(refresh, dlg.open ? 3000 : 15000);
  }

  async function open() {
    try { projects = (await api("/api/projects")).filter((p) => p.chapters > 0); } catch (e) { return R.notify(e.message); }
    if (!picked.length) picked = q.items.length ? q.items.map((it) => it.project) : [];
    await refresh();
    draw();
    dlg.showModal();
  }

  // ---- the queue being made: which projects, in what order, when ----

  function editor() {
    const ids = new Set(projects.map((p) => p.id));
    picked = picked.filter((id) => ids.has(id));
    const order = [...picked, ...projects.map((p) => p.id).filter((id) => !picked.includes(id))];
    const byId = Object.fromEntries(projects.map((p) => [p.id, p]));
    const move = (id, d) => {
      const i = picked.indexOf(id), j = i + d;
      if (i < 0 || j < 0 || j >= picked.length) return;
      [picked[i], picked[j]] = [picked[j], picked[i]];
      draw();
    };
    const rows = order.map((id) => {
      const p = byId[id], on = picked.includes(id), n = picked.indexOf(id);
      return el("div", { class: "oq-row oq-pick" + (on ? " on" : "") },
        el("label", { class: "check" }, el("input", { type: "checkbox", checked: on, onchange: (e) => {
          picked = e.target.checked ? [...picked, id] : picked.filter((x) => x !== id);
          draw();
        } }), on ? el("span", { class: "oq-n" }, `${n + 1}.`) : null, el("b", {}, p.name)),
        el("span", { class: "muted" }, `${plural(p.chapters, "chapter")}`),
        el("span", { class: "oq-move" },
          el("button", { type: "button", class: "ghost small", disabled: !on || n === 0, title: "Earlier", onclick: () => move(id, -1) }, "↑"),
          el("button", { type: "button", class: "ghost small", disabled: !on || n === picked.length - 1, title: "Later", onclick: () => move(id, 1) }, "↓")));
    });
    const time = (key, useKey, text) => el("label", { class: "check oq-time" },
      el("input", { type: "checkbox", checked: form[useKey], onchange: (e) => { form[useKey] = e.target.checked; draw(); } }),
      text, el("input", { type: "time", value: form[key], disabled: !form[useKey], oninput: (e) => { form[key] = e.target.value; } }));
    const radio = (value, title, about) => el("label", { class: `setup-choice${form.onAsk === value ? " on" : ""}` },
      el("input", { type: "radio", name: "oq-ask", checked: form.onAsk === value, onchange: () => { form.onAsk = value; draw(); } }),
      el("div", {}, el("b", {}, title), el("div", { class: "muted" }, about)));
    return [
      el("span", { class: "hint" }, "Each project gets Make the recap: whatever isn't done yet, as its own button does. " +
        "Tick the projects and put them in the order to run."),
      projects.length ? el("div", { class: "oq-list" }, ...rows) : el("span", { class: "muted" }, "No project has chapters yet."),
      el("span", { class: "field-head" }, "When"),
      time("start", "useStart", "Start at "),
      el("span", { class: "hint" }, form.useStart ? "It waits until then (the app has to stay open)." : "It starts now."),
      time("stop", "useStop", "Stop at "),
      el("span", { class: "hint" }, form.useStop ? "The project running then is paused: Resume it when you're back. No other one starts."
        : "It runs until every project has had its turn."),
      el("span", { class: "field-head" }, "When a project stops to ask you something"),
      el("span", { class: "hint" }, "Names to check, the scan group's pages, a problem with your script writer or voice."),
      el("div", { class: "setup-choices" },
        radio("skip", "Leave it for the morning", "It stops there (nothing it did is lost) and the next project starts. " +
          "Answer it, then Continue the recap."),
        radio("wait", "Wait for me", "The queue waits until you answer it.")),
    ];
  }

  // ---- the queue running, or how the last one went ----

  function progress() {
    const head = q.state === "waiting" ? `Starts at ${q.start_clock}${q.stop_clock ? `, stops at ${q.stop_clock}` : ""}.`
      : q.state === "running" ? `Running${q.stop_clock ? ` until ${q.stop_clock}` : ""}.`
      : `${q.why || "Finished"}${q.ended_clock ? ` (${q.ended_clock})` : ""}.`;
    return [
      el("span", { class: "hint" }, head),
      el("div", { class: "oq-list" }, ...q.items.map((it, i) => {
        const live = it.live && it.live.status ? ` · ${it.live.message || it.live.step || ""}${it.live.progress ? ` (${Math.round(it.live.progress * 100)}%)` : ""}` : "";
        return el("div", { class: `oq-row oq-${it.status.replace(" ", "-")}` },
          el("span", {}, el("span", { class: "oq-n" }, `${i + 1}.`), el("b", {}, it.name)),
          el("span", { class: "oq-status" }, (STATUS[it.status] || it.status) + live),
          it.note && it.status !== "running" ? el("span", { class: "muted oq-note" }, it.note) : null);
      })),
    ];
  }

  function draw() {
    const on = ["waiting", "running"].includes(q.state);
    body.replaceChildren(el("div", { class: "setup-page" },
      ...(on || (q.items.length && q.state !== "idle" && !editing) ? progress() : editor())));
    const btn = (text, cls, fn) => el("button", { type: "button", class: cls, onclick: fn }, text);
    actions.replaceChildren(el("button", { value: "cancel", class: "ghost" }, "Close"), el("span", { class: "grow" }),
      ...(on ? [btn("Stop the queue", "ghost danger", stop)]
        : q.items.length && q.state !== "idle" && !editing ? [btn("New queue", "", () => { editing = true; draw(); })]
        : [btn(form.useStart ? "Start then" : "Start the queue", "primary", start)]));
  }

  async function start() {
    if (!picked.length) return R.notify("Tick at least one project.", "Nothing to run");
    if ((form.useStart && !form.start) || (form.useStop && !form.stop)) return R.notify("Fill in the time, or untick it.", "Which time?");
    try {
      q = await api(`${BASE}/start`, { method: "POST", json: { projects: picked,
        start_at: form.useStart ? form.start : null, stop_at: form.useStop ? form.stop : null, on_ask: form.onAsk } });
    } catch (e) { return R.notify(e.message, "The queue didn't start"); }
    editing = false;
    refresh();
  }

  async function stop() {
    if (!await R.ask("Stop the queue?", "No other project starts. The one running now goes on to the end of its job.", { ok: "Stop the queue" })) return;
    q = await api(`${BASE}/stop`, { method: "POST" });
    refresh();
  }

  dlg.addEventListener("close", () => { editing = false; refresh(); });
  refresh();
})();
