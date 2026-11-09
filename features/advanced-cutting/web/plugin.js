// Advanced Cutting, a Recap Studio feature plugin: Cut the video… with the finished video's buttons (how to cut it,
// where the pieces go, and Cut), the same choices in Settings as cards with what happened, and a choice in Project
// settings to cut each video when it's made. The cutting is __init__.py's.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el } = R;
  const BASE = "/api/plugins/advanced-cutting";
  let forms = 0;  // (each form's radio buttons get a name of their own)

  R.addProjectSetting({ key: "advanced-cutting.auto", label: "Cut its video into pieces when it's made:",
    options: [["no", "No"], ["yes", "Yes, as set in Settings → Advanced Cutting"]],
    hint: "Cut the video… in the Video tab cuts it when you like; this cuts it each time it's made." });

  const size = (n) => (n >= 1e8 ? `${(n / 1e9).toFixed(2)} GB` : n >= 1e6 ? `${Math.round(n / 1e6)} MB`
    : `${Math.max(1, Math.round(n / 1e3))} KB`);
  const putSettings = (change) => api(`${BASE}/settings`, { method: "PUT", json: { changes: change } });

  // the choices: how to cut it (the three ways), and where the pieces go with what goes with them; save(change) keeps
  // a change. Two parts, so Settings can put each in a card of its own and the window lays them out as one page.
  function choices(st, save) {
    const s = st.settings, group = `ac-by-${++forms}`, radios = {};
    // (step "any": 1.5 GB must be a valid value, and the server rounds what's typed; a browser's own step check
    // would otherwise refuse the form, Close included)
    const num = (by, key, step, min) => el("input", { type: "number", value: s[key], step, min, class: "ac-num",
      onchange: (e) => { radios[by].checked = true; save({ [key]: e.target.value, by }); } });
    // a way: the radio, its words, the number and its unit, in columns that line up
    const way = (by, words, input, unit) => el("label", { class: "check ac-way" },
      radios[by] = el("input", { type: "radio", name: group, checked: s.by === by, onchange: () => save({ by }) }),
      el("span", {}, words), input, el("span", { class: "muted" }, unit));
    const tick = (key, label) => el("label", { class: "check" },
      el("input", { type: "checkbox", checked: !!s[key], onchange: (e) => save({ [key]: e.target.checked }) }), ` ${label}`);
    const example = st.windows === false ? "/mnt/nas/Pieces" : "D:\\Pieces";
    return {
      how: [
        el("div", { class: "ac-ways" },
          way("size", "By size: each piece under", num("size", "size_gb", "any", "0.05"), "GB"),
          way("chapters", "By chapters: each piece has", num("chapters", "chapters", "1", "1"), "chapters"),
          way("length", "By length: each piece up to", num("length", "hours", "any", "0.05"), "hours")),
        el("span", { class: "hint" }, "Each piece starts where a chapter starts; a chapter too big for one piece is cut " +
          "inside it. Nothing is encoded again, so it's quick, and the whole video stays as it is. A GB here is " +
          "1,000,000,000 bytes, so a piece is under its size however it's counted (Windows shows 1.5 GB as 1.39 GB)."),
      ],
      where: [
        el("div", { class: "pk-row" },
          el("input", { value: s.folder, spellcheck: "false", placeholder: `The project's output folder (in Pieces); or a ` +
            `folder${st.server ? " on the server" : ""}, e.g. ${example}`,
          onchange: (e) => save({ folder: e.target.value }) }),
          el("button", { type: "button", class: "ghost small", onclick: async () => {
            const picked = await R.pickFolder(s.folder);
            if (picked) save({ folder: picked });
          } }, "Choose…")),
        el("div", { class: "pk-ticks" }, s.folder ? tick("own_folder", "In a folder named after the project") : null,
          tick("srt", "Their subtitles (.srt)"), tick("chapter_list", "A YouTube chapter list for each")),
      ],
    };
  }

  // the cut being made and the ones waiting, as a list; stop(project) stops one
  function working(st, onlyFor = null) {
    const rows = [];
    if (st.now && (!onlyFor || st.now.project === onlyFor)) {
      rows.push(el("div", { class: "pk-item" },
        el("div", { class: "pk-main" }, el("b", {}, st.now.name), el("span", { class: "muted" }, `${st.now.message} (${Math.round(st.now.progress * 100)}%)`)),
        el("span", { class: "pk-tools" }, el("button", { type: "button", class: "ghost small", onclick: () => stop(st.now.project) }, "Stop"))));
    }
    for (const w of st.waiting.filter((x) => !onlyFor || x.project === onlyFor)) {
      rows.push(el("div", { class: "pk-item" },
        el("div", { class: "pk-main" }, el("b", {}, w.name), el("span", { class: "muted" }, "waiting its turn" +
          (onlyFor && st.now ? ` (${st.now.name} is being cut)` : ""))),
        el("span", { class: "pk-tools" }, el("button", { type: "button", class: "ghost small", onclick: () => stop(w.project) }, "Take it off"))));
    }
    return rows.length ? [el("div", { class: "pk-list" }, ...rows)] : [];
  }

  async function stop(project) {
    try { await api(`${BASE}/stop?project=${encodeURIComponent(project)}`, { method: "POST" }); refresh(); }
    catch (e) { R.notify(e.message); }
  }

  // ---- Settings → Advanced Cutting ----

  let settingsBox = null;
  R.addSettings("Advanced Cutting", async (box) => {
    settingsBox = box;
    let st;
    try { st = await api(`${BASE}/status`); } catch (e) { box.replaceChildren(el("span", { class: "error" }, e.message)); return; }
    drawSettings(box, st);
  });

  function drawSettings(box, st) {
    const error = el("div", { class: "error" });
    const save = async (change) => {
      try { drawSettings(box, await putSettings(change)); } catch (e) { error.textContent = e.message; }
    };
    const { how, where } = choices(st, save);
    const busy = working(st);
    box.replaceChildren(...[
      el("span", { class: "hint" }, "Cuts a finished video into pieces: from the Video tab (Cut the video…), or each " +
        "time a project's video is made, when its own settings say so."),
      el("section", { class: "pk-card" }, el("h3", {}, "How to cut it"), ...how),
      el("section", { class: "pk-card" }, el("h3", {}, "Put the pieces in"), ...where),
      error,
      busy.length || st.log.length ? el("section", { class: "pk-card" }, el("h3", {}, "Cuts", busy.length ? el("span", { class: "side-chip on" }, "cutting") : null),
        ...busy,
        st.log.length ? el("div", { class: "pk-log" }, ...st.log.slice(0, 8).map((x) =>
          el("div", {}, el("span", { class: "pk-at" }, x.at), el("span", {}, el("b", {}, `${x.project}: `), x.note)))) : null) : null,
    ].filter(Boolean));
    if (st.now || st.waiting.length) {  // followed while something's being cut (not while you're changing a choice)
      setTimeout(async () => {
        if (!box.isConnected || box.contains(document.activeElement)) return;
        try { drawSettings(box, await api(`${BASE}/status`)); } catch {}
      }, 2000);
    }
  }

  // ---- the Video tab: Cut the video… ----

  const dlg = el("dialog", { class: "wide-dialog ac-dialog" });
  const body = el("div", { class: "setup-body" }), actions = el("div", { class: "import-actions" });
  // (novalidate: Close closes whatever is in the boxes; what's typed is checked by the server as it's saved)
  dlg.append(el("form", { method: "dialog", class: "import-form", novalidate: true },
    el("div", { class: "setup-head" }, el("h2", {}, "Cut the video")), body, actions));
  document.body.append(dlg);
  let timer = null, error = "", typed = null;  // typed: a folder refused, kept in its box to be put right

  const statusOf = (p) => api(`${BASE}/status?project=${encodeURIComponent(p.id)}`);

  R.addButton("video", "Cut the video…", async () => {
    const p = R.current();
    if (!p) return;
    error = "";
    typed = null;
    try { drawDialog(await statusOf(p)); } catch (e) { return R.notify(e.message); }
    dlg.showModal();
    clearInterval(timer);
    timer = setInterval(() => { if (dlg.open) refresh(); }, 1500);
  }, { title: "Cut this video into pieces: by size, by chapters or by length" });
  dlg.addEventListener("close", () => { clearInterval(timer); timer = null; });

  const typing = (box) => { const a = document.activeElement;
    return !!a && box.contains(a) && a.matches("input[type=number], input:not([type])"); };

  async function refresh() {
    const p = R.current();
    if (dlg.open && p) {
      if (typing(body)) return;  // (drawn again once the change is made)
      try { drawDialog(await statusOf(p)); } catch (e) { error = e.message; }
    } else if (settingsBox && settingsBox.isConnected) {
      try { drawSettings(settingsBox, await api(`${BASE}/status`)); } catch {}
    }
  }

  function drawDialog(st) {
    const p = R.current(), mine = st.project;
    const busy = (st.now && st.now.project === p.id) || st.waiting.some((w) => w.project === p.id);
    const save = async (change) => {
      try { await putSettings(change); error = ""; typed = null; }
      catch (e) { error = e.message; if ("folder" in change) typed = change.folder; }
      refresh();
    };
    const pieces = mine.pieces;
    // (on a server, the pieces are on the server: each is saved from here, with its subtitles and chapter list)
    const saveLink = (name, label) => el("a", { href: `${BASE}/piece/${encodeURIComponent(p.id)}?name=${encodeURIComponent(name)}`,
      download: name, class: "link", title: `Save ${name}` }, label);
    const pieceName = (x) => !st.server ? el("span", {}, x.name) : el("span", {}, saveLink(x.name, x.name),
      ...(x.srt ? [" · ", saveLink(x.srt, "subtitles")] : []), ...(x.chapters ? [" · ", saveLink(x.chapters, "chapter list")] : []));
    const { how, where } = choices(typed === null ? st : { ...st, settings: { ...st.settings, folder: typed } }, save);
    body.replaceChildren(el("div", { class: "setup-page" }, ...[
      mine.has_video ? null : el("span", { class: "hint warn" }, "This project has no video yet: render it first."),
      ...how,
      el("h4", {}, "Put the pieces in"),
      ...where,
      el("span", { class: "hint" }, "These are also what's used when a video is cut by itself (the project's own settings)."),
      error ? el("div", { class: "error" }, error) : null,
      ...working(st, p.id),
      pieces.length ? el("h4", {}, `Its last cut: ${R.plural(pieces.length, "piece")}`) : null,
      pieces.length ? el("span", { class: "hint ac-folder" }, st.server ? `In ${mine.folder}, on the server. Click one to ` +
        "save it on this device." : `In ${mine.folder}`) : null,
      pieces.length ? el("div", { class: "pk-list" }, ...pieces.map((x) =>
        el("div", { class: "pk-item" }, pieceName(x), el("span", { class: "pk-state" }, size(x.size))))) : null].filter(Boolean)));
    actions.replaceChildren(el("button", { value: "cancel", class: "ghost", formnovalidate: true }, "Close"), el("span", { class: "grow" }),
      // (not while a folder typed in is refused: the cut would go in the folder saved before)
      el("button", { type: "button", class: "primary", disabled: busy || !mine.has_video || typed !== null, onclick: async () => {
        try { await api(`${BASE}/cut/${p.id}`, { method: "POST" }); error = ""; }
        catch (e) { error = e.message; }
        refresh();
      } }, pieces.length ? "Cut it again" : "Cut"));
  }
})();
