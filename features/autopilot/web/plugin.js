// Autopilot, a Recap Studio feature plugin: a sidebar button opens the cockpit. The flight: what it's doing now, what
// the last dry run would do, and its log. The settings: when, which charts, how much, and YouTube (__init__.py).
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el, plural } = R;
  const BASE = "/api/plugins/autopilot";
  const STATUS = { waiting: "Waiting", getting: "Getting chapters", got: "Chapters got", recapping: "Making the recap",
                   done: "Done", failed: "Failed", left: "Left for you", skipped: "Not started" };

  let st = null, view = "flight", form = null, timer = null, settingsBox = null;

  const button = R.addButton("sidebar", "Autopilot…", open, { title: "Sit back: a chart of your sources, the series still coming out, their newest chapters, recapped" });
  const dlg = el("dialog", { class: "wide-dialog ap-dialog" });
  const body = el("div", { class: "setup-body" });
  const actions = el("div", { class: "import-actions" });
  dlg.append(el("form", { method: "dialog", class: "import-form" },
    el("div", { class: "setup-head" }, el("h2", {}, "Autopilot")), body, actions));
  document.body.append(dlg);

  function label() {
    if (!st) return;
    const run = st.run;
    if (run && run.stage === "planning") button.textContent = "Autopilot: planning…";
    else if (run) {
      const over = run.plan.filter((e) => ["done", "failed", "left", "skipped"].includes(e.status)).length;
      button.textContent = `Autopilot: ${Math.min(over + 1, run.plan.length)} of ${run.plan.length}`;
    } else button.textContent = st.settings.on ? "Autopilot ✈" : "Autopilot…";
  }

  async function refresh() {
    try { st = await api(`${BASE}/status`); } catch { return; }
    label();
    if (dlg.open) draw();
    if (settingsBox) drawSettings(settingsBox);
    clearTimeout(timer);
    timer = setTimeout(refresh, st.run ? (dlg.open ? 3000 : 15000) : 60000);
  }

  async function open() {
    await refresh();
    if (!st) return R.notify("Autopilot didn't answer.");
    view = st.settings.charts.length ? "flight" : "settings";
    draw();
    dlg.showModal();
  }

  // ---- the flight ----

  function entryRow(e, i) {
    const live = e.live && e.live.status ? ` · ${e.live.message || e.live.step || ""}${e.live.progress ? ` (${Math.round(e.live.progress * 100)}%)` : ""}` : "";
    const what = e.chapters && e.chapters.length ? `${plural(e.chapters.length, "chapter")}${e.new ? ", a new project" : ""}`
      : e.ids && e.ids.length ? `${plural(e.ids.length, "chapter")} got earlier` : "";
    return el("div", { class: `ap-row ap-${e.status}` },
      el("span", { class: "ap-n" }, `${i + 1}.`),
      el("span", {}, el("b", {}, e.title), what ? el("span", { class: "muted" }, ` · ${what}`) : null),
      el("span", { class: "ap-status" }, (STATUS[e.status] || e.status) + live),
      e.note && !["getting", "recapping"].includes(e.status) ? el("span", { class: "ap-note" }, e.note) : null);
  }

  function flight() {
    const s = st.settings, out = [];
    const state = !s.charts.length ? "No chart picked yet: Settings."
      : !s.on ? "Off: it runs only when you say Run now." + (st.next ? "" : "")
      : st.next ? `On: next run ${st.next}.` : "On, but no day is ticked.";
    out.push(el("div", { class: "ap-head" }, el("span", { class: "ap-state" }, state),
      st.last ? el("span", { class: "muted" }, `Last run ended ${st.last}.`) : null,
      st.seen ? el("span", { class: "muted" }, `${plural(st.seen, "series")} seen so far.`) : null,
      st.leftover ? el("span", { class: "muted" }, `${plural(st.leftover, "recap")} left for the next run.`) : null));
    if (st.server) out.push(el("span", { class: "hint" }, "On this server, it runs whether or not anyone has the page open."));
    if (st.run) {
      const run = st.run;
      out.push(el("span", { class: "field-head" }, run.stage === "planning" ? `Planning (${run.why}, since ${run.started_clock})…`
        : `Flying (${run.why}, since ${run.started_clock})${run.stop ? ": stopping after this one" : ""}`));
      if (run.stage === "planning") out.push(el("span", { class: "hint" }, "Asking each chart's source for its list and the series' chapters. A minute or two."));
      else {
        out.push(el("div", { class: "ap-list" }, ...run.plan.map(entryRow)));
        if (!run.plan.length) out.push(el("span", { class: "muted" }, "Nothing to do this time."));
      }
      if (run.notes && run.notes.length) out.push(el("details", {}, el("summary", {}, `Passed over: ${run.notes.length}`),
        el("ul", { class: "ap-notes" }, ...run.notes.map((n) => el("li", {}, n)))));
    } else if (st.lineup) {
      const lu = st.lineup;
      out.push(el("span", { class: "field-head" }, "The last plan"),
        el("span", { class: "hint" }, "What a run would do, from the last dry run or the last run's start. Run now does it."));
      out.push(el("div", { class: "ap-list" }, ...lu.entries.map((e, i) => entryRow({ ...e, status: "waiting" }, i))));
      if (!lu.entries.length) out.push(el("span", { class: "muted" }, "Nothing: no series passed the sieve."));
      if (lu.notes && lu.notes.length) out.push(el("details", {}, el("summary", {}, `Passed over: ${lu.notes.length}`),
        el("ul", { class: "ap-notes" }, ...lu.notes.map((n) => el("li", {}, n)))));
    } else {
      out.push(el("span", { class: "hint" }, "Dry run shows what it would do now, without getting anything. Run now does it."));
    }
    if (st.runs && st.runs.length) {
      out.push(el("details", {}, el("summary", {}, `Past runs: ${st.runs.length}`),
        el("div", { class: "ap-log" }, ...st.runs.map((r) => el("div", {}, el("span", { class: "ap-at" }, r.started_clock),
          `${r.why}: ${r.summary}${r.grounded ? `. ${r.grounded}` : ""}`)))));
    }
    if (st.log && st.log.length) {
      out.push(el("details", { open: st.run ? true : null }, el("summary", {}, "Log"),
        el("div", { class: "ap-log" }, ...st.log.slice(0, 25).map((x) => el("div", {}, el("span", { class: "ap-at" }, x.at),
          x.series ? el("b", {}, `${x.series}: `) : null, x.note)))));
    }
    return out;
  }

  // ---- the settings ----

  function startForm() {
    const s = st.settings;
    form = { ...s, days: [...s.days], charts: s.charts.map((c) => `${c.key}|${c.list}`), skip: s.skip.join("\n") };
  }

  function settings() {
    if (!form) startForm();
    const num = (key, hint, min = 1) => el("label", {}, el("input", { type: "number", value: form[key], min, max: 365,
      oninput: (e) => { form[key] = +e.target.value; } }), hint ? el("span", { class: "muted" }, ` ${hint}`) : null);
    const charts = [];
    for (const src of st.charts) {
      const blocked = src.nsfw && !form.nsfw;
      charts.push(el("div", { class: "ap-source" }, src.name, src.nsfw ? el("span", { class: "muted" }, blocked ? " · NSFW, allowed below" : " · NSFW") : null));
      for (const l of src.lists) {
        const id = `${src.key}|${l.id}`;
        charts.push(el("label", { class: "check" }, el("input", { type: "checkbox", checked: form.charts.includes(id), disabled: blocked,
          onchange: (e) => { form.charts = e.target.checked ? [...form.charts, id] : form.charts.filter((x) => x !== id); } }), l.name));
      }
    }
    const yt = st.youtube;
    return [
      el("label", { class: "check" }, el("input", { type: "checkbox", checked: form.on, onchange: (e) => { form.on = e.target.checked; } }),
        el("b", {}, "On"), el("span", { class: "muted" }, " · runs on the days and at the time below while the app is open")),
      el("span", { class: "field-head" }, "When"),
      el("div", { class: "ap-days" }, ...st.days.map((d, i) => el("label", { class: "check" }, el("input", { type: "checkbox", checked: form.days.includes(i),
        onchange: (e) => { form.days = e.target.checked ? [...form.days, i].sort() : form.days.filter((x) => x !== i); } }), d))),
      el("label", {}, "At ", el("input", { type: "time", value: form.time, oninput: (e) => { form.time = e.target.value; } }),
        el("span", { class: "muted" }, " · a time the app was closed at is made up for when it next opens")),
      el("span", { class: "field-head" }, "Charts"),
      el("span", { class: "hint" }, "The lists your own sources offer (Settings → Sources). Each run reads each ticked list's first page."),
      charts.length ? el("div", { class: "ap-charts" }, ...charts) : el("span", { class: "muted" }, "None of your sources has a list. Add a source that does (Settings → Sources)."),
      el("span", { class: "field-head" }, "How much"),
      el("div", { class: "ap-grid" },
        el("span", {}, "Series from each chart (the first from the top that pass)"), num("top"),
        el("span", {}, "Still coming out: a chapter in the last"), num("fresh_days", "days (0: don't look at dates)", 0),
        el("span", {}, "A series seen for the first time: its newest"), num("first", "chapters"),
        el("span", {}, "A series seen before: at most"), num("each", "new chapters a run"),
        el("span", {}, "Chapters a run, in all"), num("budget", "(your script writer's and voice's bill)")),
      el("label", { class: "check" }, el("input", { type: "checkbox", checked: form.nsfw, onchange: (e) => { form.nsfw = e.target.checked; draw(); } }),
        "Charts of NSFW sources too"),
      el("span", { class: "field-head" }, "Skip"),
      el("textarea", { placeholder: "One series a line: any series whose title has these words is passed over", oninput: (e) => { form.skip = e.target.value; } }, form.skip),
      el("span", { class: "field-head" }, "The recap"),
      el("div", { class: "setup-choices" },
        ...[["new", "The new chapters, their own video", "Make the recap for just the chapters it got, as New chapters on a schedule does."],
            ["all", "The whole project", "Make the recap for everything in the project that isn't done yet."]].map(([v, t, about]) =>
          el("label", { class: `setup-choice${form.recap === v ? " on" : ""}` },
            el("input", { type: "radio", name: "ap-recap", checked: form.recap === v, onchange: () => { form.recap = v; draw(); } }),
            el("div", {}, el("b", {}, t), el("div", { class: "muted" }, about))))),
      el("span", { class: "field-head" }, "YouTube"),
      !yt.installed ? el("span", { class: "hint" }, "Install Post to YouTube (Settings → Plugins) and connect your channel, and the videos it makes can go up by themselves.")
        : !yt.connected ? el("span", { class: "hint" }, "Post to YouTube is on but no channel is connected yet (Settings → Post to YouTube).")
        : el("div", {},
          el("label", { class: "check" }, el("input", { type: "checkbox", checked: form.youtube, onchange: (e) => { form.youtube = e.target.checked; draw(); } }),
            "Post each new project's video when it's made"),
          form.youtube ? el("label", {}, " as ", el("select", { onchange: (e) => { form.privacy = e.target.value; } },
            ...["private", "unlisted", "public"].map((v) => el("option", { value: v, selected: form.privacy === v }, v)))) : null,
          el("span", { class: "hint" }, "Set on the projects it makes (Post to YouTube's choice for each); projects it finds already made keep their own.")),
    ];
  }

  async function save() {
    const settings = { ...form, charts: form.charts.map((x) => { const i = x.indexOf("|"); return { key: x.slice(0, i), list: x.slice(i + 1) }; }),
      skip: form.skip.split("\n").map((x) => x.trim()).filter(Boolean) };
    try { st = await api(`${BASE}/settings`, { method: "PUT", json: { settings } }); } catch (e) { return R.notify(e.message, "Not saved"); }
    form = null;
    view = "flight";
    label();
    draw();
  }

  function draw() {
    const run = st.run;
    body.replaceChildren(el("div", { class: "ap-views" },
      ...[["flight", "Flight"], ["settings", "Settings"]].map(([v, t]) => el("button", { type: "button", class: view === v ? "" : "ghost",
        onclick: () => { view = v; if (v === "settings") form = null; draw(); } }, t))),
      el("div", { class: "setup-page" }, ...(view === "flight" ? flight() : settings())));
    const btn = (text, cls, fn) => el("button", { type: "button", class: cls, onclick: fn }, text);
    actions.replaceChildren(el("button", { value: "cancel", class: "ghost" }, "Close"), el("span", { class: "grow" }),
      ...(view === "settings" ? [btn("Save", "primary", save)]
        : run ? [btn("Stop after this one", "ghost danger", stop)]
        : [btn("Dry run", "ghost", () => go(true)), btn("Run now", "primary", () => go(false))]));
  }

  async function go(dry) {
    if (!st.settings.charts.length) { view = "settings"; return draw(); }
    try { st = await api(`${BASE}/run`, { method: "POST", json: { dry } }); } catch (e) { return R.notify(e.message, "It didn't start"); }
    label();
    draw();
    clearTimeout(timer);
    timer = setTimeout(refresh, 2000);
  }

  async function stop() {
    if (!await R.ask("Stop the run?", "No other series starts. The job going on now finishes; chapters got but not recapped are first next time.", { ok: "Stop" })) return;
    try { st = await api(`${BASE}/stop`, { method: "POST", json: {} }); } catch (e) { return R.notify(e.message); }
    draw();
  }

  // ---- Settings → Autopilot: a line and the way in ----

  function drawSettings(box) {
    if (!st) return;
    const s = st.settings;
    const line = !s.charts.length ? "Not set up yet." : s.on ? `On${st.next ? `, next run ${st.next}` : ""}.` : "Off (Run now still works).";
    box.replaceChildren(el("span", { class: "hint" }, `${line}${st.last ? ` Last run ended ${st.last}.` : ""}`),
      el("div", {}, el("button", { type: "button", onclick: open }, "Open Autopilot…")));
  }

  R.addSettings("Autopilot", (box) => { settingsBox = box; drawSettings(box); if (!st) refresh(); });

  dlg.addEventListener("close", () => { form = null; refresh(); });
  refresh();
})();
