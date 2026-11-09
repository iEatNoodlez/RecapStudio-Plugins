// Autopilot, a Recap Studio feature plugin: a panel on the home page (the next flight, the last one, Dry run and Fly
// now), a rail button with the next flight's time as its chip, and a card in Settings: each opens the cockpit. The
// flight: what it's doing now, what the last dry run would do, and its log. The settings: when, which charts, how
// much, and YouTube (__init__.py). Built from the app's plugin kit (.pk-*).
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el, plural } = R;
  const BASE = "/api/plugins/autopilot";
  const STATUS = { waiting: "Waiting", getting: "Getting chapters", got: "Chapters got", recapping: "Making the recap",
                   done: "Done", failed: "Failed", left: "Left for you", skipped: "Not started" };
  const TONE = { done: "ok", failed: "bad", left: "bad", getting: "on", recapping: "on" };

  let st = null, view = "flight", form = null, timer = null, settingsBox = null, panel = null;

  const button = R.addButton("sidebar", "Autopilot", open, { title: "Sit back: a chart of your sources, the series still coming out, their newest chapters, recapped" });
  // the home page's panel (apps from 0.4.3; an older app has the rail's button alone)
  panel = R.addLibraryPanel ? R.addLibraryPanel("Autopilot", drawPanel) : null;
  const dlg = el("dialog", { class: "wide-dialog ap-dialog" });
  const body = el("div", { class: "setup-body" });
  const actions = el("div", { class: "import-actions" });
  dlg.append(el("form", { method: "dialog", class: "import-form", novalidate: true },
    el("div", { class: "setup-head" }, el("h2", {}, "Autopilot")), body, actions));
  document.body.append(dlg);

  // "Tue 10 Nov 03:00" as the rail's chip: "Tue 03:00"
  const shortClock = (clock) => String(clock || "").replace(/^(\w{3}) \d\d \w{3} /, "$1 ");
  const flying = (run) => Math.min(run.plan.filter((e) => ["done", "failed", "left", "skipped"].includes(e.status)).length + 1, run.plan.length);
  const btn = (text, cls, fn) => el("button", { type: "button", class: cls, onclick: fn }, text);
  const card = (title, ...kids) => el("section", { class: "pk-card" }, el("h3", {}, title), ...kids.filter(Boolean));

  // the rail's button: its chip says what Autopilot is up to (an older app, without chips, gets the words)
  function label() {
    if (!st) return;
    const run = st.run, s = st.settings;
    if (button.setStatus) {
      if (run && run.stage === "planning") button.setStatus("planning…", "on");
      else if (run) button.setStatus(`${flying(run)} of ${run.plan.length}`, "on");
      else button.setStatus(s.on ? (st.next ? shortClock(st.next) : "on") : s.charts.length ? "off" : "");
    } else {
      button.textContent = run && run.stage === "planning" ? "Autopilot: planning…" : run ? `Autopilot: ${flying(run)} of ${run.plan.length}`
        : s.on ? "Autopilot ✈" : "Autopilot…";
    }
    if (panel) drawPanel(panel.box);
    if (settingsBox) drawSettings(settingsBox);
  }

  // ---- where it stands, in a few lines: the home page's panel and the Settings card say the same ----

  function summary() {
    const s = st.settings, run = st.run, out = [];
    const picked = s.charts.map((c) => {
      const src = st.charts.find((a) => a.key === c.key), l = src && src.lists.find((x) => x.id === c.list);
      return src && l ? `${l.name} on ${src.name}` : null;
    }).filter(Boolean);
    const how = `${picked.join(", ") || plural(s.charts.length, "chart")}, the top ${s.top} still coming out, ${plural(s.budget, "chapter")} at most`;
    if (run && run.stage === "planning") {
      out.push(el("div", {}, el("b", {}, "Planning the flight"), ` (${run.why}, since ${run.started_clock}): asking each chart's source for its list.`));
    } else if (run) {
      const now = run.plan.find((e) => ["getting", "recapping"].includes(e.status));
      out.push(el("div", {}, el("b", {}, `Flying: ${flying(run)} of ${run.plan.length}`),
        now ? ` · ${now.title}: ${(STATUS[now.status] || now.status).toLowerCase()}` : "", run.stop ? " · stopping after this one" : ""));
    } else if (!s.charts.length) {
      out.push(el("div", {}, "Not set up yet. Pick a chart of your sources in its settings, and on the days you choose it gets the newest chapters of the series still coming out and makes their recaps."));
    } else if (s.on && st.next) out.push(el("div", {}, `Next flight ${st.next}: ${how}.`));
    else if (s.on) out.push(el("div", {}, `On, but no day is ticked. A flight: ${how}.`));
    else out.push(el("div", {}, `Off: Fly now still flies once (${how}).`));
    const last = st.runs && st.runs[0];
    if (last) out.push(el("div", {}, `Last flight ${last.started_clock}: ${last.summary}${last.grounded ? `. ${last.grounded}` : ""}.`));
    if (st.leftover) out.push(el("div", {}, `${plural(st.leftover, "recap")} left for the next flight.`));
    return out;
  }
  const chipWords = () => [st.run ? "flying" : st.settings.on ? "on" : st.settings.charts.length ? "off" : "", st.run || st.settings.on ? "ok" : ""];
  const fly = (dry) => async () => { view = "flight"; await go(dry); if (!dlg.open) { draw(); dlg.showModal(); } };
  const buttons = () => st.run ? [btn("Stop after this one", "ghost danger", stop), btn("The flight…", "ghost", () => open("flight"))]
    : [btn("Dry run", "ghost", fly(true)), btn("Fly now", "ghost", fly(false)), btn("Settings", "link", () => open("settings"))];

  // the home page's panel: the next flight, the last one, Dry run and Fly now
  function drawPanel(box) {
    if (!st) return;
    box.replaceChildren(...summary(), el("div", { class: "lib-actions" }, ...buttons()));
    if (panel) panel.setStatus(...chipWords());
  }

  // Settings → Autopilot: the same lines as a card, and the way into the cockpit
  function drawSettings(box) {
    if (!st) return;
    const [words, tone] = chipWords();
    box.replaceChildren(
      el("span", { class: "hint" }, "On the days and at the time you choose, it reads a chart of your sources, takes the series " +
        "still coming out, gets their newest chapters into projects and makes the recaps. Its settings are in the cockpit."),
      el("section", { class: "pk-card" }, el("h3", {}, "The next flight", el("span", { class: `side-chip ${tone}` }, words)),
        el("div", { class: "ap-lines" }, ...summary()),
        el("div", { class: "pk-actions" }, ...buttons(), btn("Open Autopilot…", "ghost", () => open()))));
  }
  R.addSettings("Autopilot", (box) => { settingsBox = box; drawSettings(box); if (!st) refresh(); });

  async function refresh() {
    try { st = await api(`${BASE}/status`); } catch { return; }
    label();
    if (dlg.open) draw();
    clearTimeout(timer);
    timer = setTimeout(refresh, st.run ? (dlg.open ? 3000 : 15000) : 60000);
  }

  async function open(which) {  // (the rail's button passes its click)
    await refresh();
    if (!st) return R.notify("Autopilot didn't answer.");
    view = typeof which === "string" ? which : st.settings.charts.length ? "flight" : "settings";
    if (view === "settings") form = null;
    draw();
    if (!dlg.open) dlg.showModal();
  }

  // ---- the flight ----

  function entryRow(e, i) {
    const live = e.live && e.live.status ? ` · ${e.live.message || e.live.step || ""}${e.live.progress ? ` (${Math.round(e.live.progress * 100)}%)` : ""}` : "";
    const what = e.chapters && e.chapters.length ? `${plural(e.chapters.length, "chapter")}${e.new ? ", a new project" : ""}`
      : e.ids && e.ids.length ? `${plural(e.ids.length, "chapter")} got earlier` : "";
    return el("div", { class: "pk-item" },
      el("span", { class: "pk-main" }, el("span", { class: "pk-n" }, `${i + 1}.`), el("b", {}, e.title), what ? el("span", { class: "muted" }, what) : null),
      el("span", { class: `pk-state ${TONE[e.status] || ""}` }, (STATUS[e.status] || e.status) + live),
      e.note && !["getting", "recapping"].includes(e.status) ? el("span", { class: "pk-note" }, e.note) : null);
  }
  const fold = (title, open, ...kids) => el("details", { class: "details-adv", open: open ? true : null }, el("summary", {}, title), ...kids);

  function flight() {
    const s = st.settings, out = [];
    const [state, tone] = !s.charts.length ? ["No chart picked yet: Settings.", ""]
      : st.run ? [st.run.stage === "planning" ? "Planning the flight." : "Flying.", "on"]
      : !s.on ? ["Off: it runs only when you say Run now.", ""]
      : st.next ? [`On: next run ${st.next}.`, "ok"] : ["On, but no day is ticked.", "warn"];
    out.push(el("div", { class: "pk-status" }, el("span", { class: `pk-dot ${tone}` }), el("span", {}, el("b", {}, state),
      st.last ? ` Last run ended ${st.last}.` : "", st.seen ? ` ${plural(st.seen, "series")} seen so far.` : "",
      st.leftover ? ` ${plural(st.leftover, "recap")} left for the next run.` : "")));
    if (st.server) out.push(el("span", { class: "hint" }, "On this server, it runs whether or not anyone has the page open."));
    if (st.run) {
      const run = st.run;
      out.push(card(run.stage === "planning" ? `Planning (${run.why}, since ${run.started_clock})…`
        : `Flying (${run.why}, since ${run.started_clock})${run.stop ? ": stopping after this one" : ""}`,
      run.stage === "planning" ? el("span", { class: "hint" }, "Asking each chart's source for its list and the series' chapters. A minute or two.")
        : run.plan.length ? el("div", { class: "pk-list" }, ...run.plan.map(entryRow)) : el("span", { class: "pk-empty" }, "Nothing to do this time."),
      run.notes && run.notes.length ? fold(`Passed over: ${run.notes.length}`, false, el("ul", { class: "ap-notes" }, ...run.notes.map((n) => el("li", {}, n)))) : null));
    } else if (st.lineup) {
      const lu = st.lineup;
      out.push(card("The last plan",
        el("span", { class: "hint" }, "What a run would do, from the last dry run or the last run's start. Run now does it."),
        lu.entries.length ? el("div", { class: "pk-list" }, ...lu.entries.map((e, i) => entryRow({ ...e, status: "waiting" }, i)))
          : el("span", { class: "pk-empty" }, "Nothing: no series passed the sieve."),
        lu.notes && lu.notes.length ? fold(`Passed over: ${lu.notes.length}`, false, el("ul", { class: "ap-notes" }, ...lu.notes.map((n) => el("li", {}, n)))) : null));
    } else {
      out.push(el("span", { class: "hint" }, "Dry run shows what it would do now, without getting anything. Run now does it."));
    }
    if (st.runs && st.runs.length) {
      out.push(fold(`Past runs: ${st.runs.length}`, false,
        el("div", { class: "pk-log" }, ...st.runs.map((r) => el("div", {}, el("span", { class: "pk-at" }, r.started_clock),
          el("span", {}, `${r.why}: ${r.summary}${r.grounded ? `. ${r.grounded}` : ""}`))))));
    }
    if (st.log && st.log.length) {
      out.push(fold("Log", !!st.run,
        el("div", { class: "pk-log" }, ...st.log.slice(0, 25).map((x) => el("div", {}, el("span", { class: "pk-at" }, x.at),
          el("span", {}, x.series ? el("b", {}, `${x.series}: `) : null, x.note))))));
    }
    return out;
  }

  // ---- the settings ----

  function startForm() {
    const s = st.settings;
    form = { ...s, days: [...s.days], charts: s.charts.map((c) => `${c.key}|${c.list}`), skip: s.skip.join("\n") };
  }

  // Six cards in two columns: When, Charts and The recap on the left; How much, Skip and YouTube on the right.
  function settings() {
    if (!form) startForm();
    const pill = (kind, name, checked, text, onchange, { disabled = false, quiet = false } = {}) =>
      el("label", { class: `pk-pill${quiet ? " quiet" : ""}${checked ? " on" : ""}${disabled ? " off" : ""}` },
        el("input", { type: kind, name, checked, disabled, onchange }), text);
    const num = (key, min = 1) => el("input", { type: "number", class: "pk-num", value: form[key], min, max: 365,
      oninput: (e) => { form[key] = +e.target.value; } });
    // when: on, the days as pills, the time on the same line
    const when = card("When",
      el("label", { class: "check" }, el("input", { type: "checkbox", checked: form.on, onchange: (e) => { form.on = e.target.checked; } }),
        el("b", {}, "On"), el("span", { class: "muted" }, " · on these days, at this time, while the app is open")),
      el("div", { class: "pk-pills" }, ...st.days.map((d, i) => pill("checkbox", "", form.days.includes(i), d,
        (e) => { form.days = e.target.checked ? [...form.days, i].sort() : form.days.filter((x) => x !== i); draw(); }, { quiet: true }))),
      el("label", { class: "pk-line" }, "At", el("input", { type: "time", value: form.time, oninput: (e) => { form.time = e.target.value; } }),
        el("span", { class: "muted" }, "A time the app was closed at is made up for when it next opens.")));
    // charts: each source's lists as pills on one row
    const charts = st.charts.map((src) => {
      const blocked = src.nsfw && !form.nsfw;
      return el("div", { class: "ap-source-row" },
        el("span", { class: "ap-source" }, src.name, src.nsfw ? el("span", { class: "muted" }, blocked ? " · NSFW, allowed below" : " · NSFW") : null),
        el("div", { class: "pk-pills" }, ...src.lists.map((l) => {
          const id = `${src.key}|${l.id}`;
          return pill("checkbox", "", form.charts.includes(id), l.name,
            (e) => { form.charts = e.target.checked ? [...form.charts, id] : form.charts.filter((x) => x !== id); draw(); }, { disabled: blocked });
        })));
    });
    const chartsCard = card("Charts",
      el("span", { class: "hint" }, "The lists your sources offer (Settings → Sources). Each flight reads each ticked list's first page."),
      charts.length ? el("div", { class: "ap-charts" }, ...charts)
        : el("span", { class: "pk-empty" }, "None of your sources has a list. Add a source that does (Settings → Sources)."),
      el("label", { class: "check" }, el("input", { type: "checkbox", checked: form.nsfw, onchange: (e) => { form.nsfw = e.target.checked; draw(); } }),
        "Charts of NSFW sources too"));
    // how much: a label, the number and its unit on each row
    const row = (text, input, unit = "") => [el("span", {}, text), el("span", { class: "pk-amount" }, input, unit ? el("span", { class: "muted" }, ` ${unit}`) : null)];
    const howMuch = card("How much",
      el("div", { class: "pk-grid" },
        ...row("Series from each chart, the first from the top that pass", num("top")),
        ...row("Still coming out: a chapter in the last", num("fresh_days", 0), "days (0: don't look at dates)"),
        ...row("A series seen for the first time: its newest", num("first"), "chapters"),
        ...row("A series seen before: at most", num("each"), "new chapters a flight"),
        ...row("Chapters a flight, in all", num("budget"), "(your script writer's and voice's bill)")));
    const skip = card("Skip",
      el("textarea", { rows: 3, placeholder: "One series a line: any series whose title has these words is passed over",
        oninput: (e) => { form.skip = e.target.value; } }, form.skip));
    const recap = card("The recap",
      el("div", { class: "pk-pills" },
        pill("radio", "ap-recap", form.recap === "new", "The new chapters, their own video", () => { form.recap = "new"; draw(); }),
        pill("radio", "ap-recap", form.recap === "all", "The whole project", () => { form.recap = "all"; draw(); })),
      el("span", { class: "hint" }, form.recap === "new" ? "Make the recap for just the chapters it got, as New chapters on a schedule does."
        : "Make the recap for everything in the project that isn't done yet."));
    const yt = st.youtube;
    const youtube = card("YouTube",
      !yt.installed ? el("span", { class: "hint" }, "Install Post to YouTube (Settings → Plugins) and connect your channel, and the videos it makes can go up by themselves.")
        : !yt.connected ? el("span", { class: "hint" }, "Post to YouTube is on but no channel is connected yet (Settings → YouTube).")
        : el("div", { class: "pk-row" },
          el("label", { class: "check" }, el("input", { type: "checkbox", checked: form.youtube, onchange: (e) => { form.youtube = e.target.checked; draw(); } }),
            "Post each new project's video when it's made"),
          form.youtube ? el("label", { class: "pk-line" }, "as", el("select", { onchange: (e) => { form.privacy = e.target.value; } },
            ...["private", "unlisted", "public"].map((v) => el("option", { value: v, selected: form.privacy === v }, v)))) : null),
      yt.installed && yt.connected ? el("span", { class: "hint" }, "Set on the projects it makes (Post to YouTube's choice for each); projects it finds already made keep their own.") : null);
    return [el("div", { class: "pk-cols" },
      el("div", { class: "pk-col" }, when, chartsCard, recap),
      el("div", { class: "pk-col" }, howMuch, skip, youtube))];
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
    body.replaceChildren(el("div", { class: "src-modes pk-views", role: "tablist" },
      ...[["flight", "Flight"], ["settings", "Settings"]].map(([v, t]) => el("button", { type: "button", role: "tab", class: view === v ? "on" : "",
        onclick: () => { view = v; if (v === "settings") form = null; draw(); } }, t))),
    el("div", { class: "setup-page" }, ...(view === "flight" ? flight() : settings())));
    actions.replaceChildren(el("button", { value: "cancel", class: "ghost", formnovalidate: true }, "Close"), el("span", { class: "grow" }),
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
    label();
    draw();
  }

  dlg.addEventListener("close", () => { form = null; refresh(); });
  refresh();
})();
