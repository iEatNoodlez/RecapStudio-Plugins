// Find the series, a Recap Studio feature plugin (a page script only). New project gets a search box that asks every
// source that can search (AniList from Recap Studio's plugin list, and any source plugin of your own); the series
// picked names the project and suggests what kind of comic it is, and its characters can be ticked on the dialog's
// Characters page and added with their pictures. A series picked from a source with chapters (a source plugin of
// yours for its site) gets them too, once the project is made, unless that's unticked. The Characters tab gets the
// same characters for a project that already exists.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el, plural } = R;
  const TICKED = 15;  // main characters, then supporting ones up to this many, start ticked
  const GET = "recap-find-series-chapters";  // whether "Get its chapters" was last left ticked, in this window's storage
  const getChosen = () => { try { return localStorage.getItem(GET) !== "no"; } catch { return true; } };

  // one search: what was typed, the results by source, and the series picked with its characters
  const finder = (query = "") => ({ query, results: null, status: "", ran: false, pick: null, cast: null, castStatus: "",
    getChapters: getChosen() });
  const key = (name) => name.trim().toLowerCase();

  async function runSearch(f, draw) {
    const q = f.query.trim();
    if (!q) return;
    Object.assign(f, { ran: true, status: "Searching…", results: null });
    draw();
    let sources;
    try { sources = (await api("/api/sources")).sources.filter((s) => s.search && s.kind === "plugin"); }
    catch (e) { f.status = e.message; return draw(); }
    if (!sources.length) {
      f.status = "None of your sources can search: install AniList from Recap Studio's plugins (Settings → Plugins).";
      return draw();
    }
    sources.sort((a, b) => (b.characters ? 1 : 0) - (a.characters ? 1 : 0));  // the ones with characters first
    f.results = await Promise.all(sources.map((s) =>
      api("/api/sources/search", { method: "POST", json: { key: s.key, query: q, page: 1 } })
        .then((r) => ({ source: s, items: r.items.slice(0, 8) }), (e) => ({ source: s, items: [], error: e.message }))));
    f.status = f.results.some((r) => r.items.length) ? "" : `Nothing found for "${q}".`;
    draw();
  }

  async function pick(f, source, item, draw) {
    Object.assign(f, { pick: { source, item }, cast: null, castStatus: source.characters ? "Getting its characters…" : "" });
    draw();
    if (!source.characters) return;
    try {
      const found = (await api("/api/sources/characters", { method: "POST", json: { key: source.key, ref: item.ref } })).characters;
      let ticked = 0;
      f.cast = found.map((c) => {
        const on = c.role === "main" || (c.role === "supporting" && ticked < TICKED);
        if (on) ticked++;
        return { ...c, on };
      });
      f.castStatus = found.length ? "" : `${source.name} lists no characters for it.`;
    } catch (e) { f.castStatus = e.message; }
    draw();
  }

  // the search box and its results, or the series picked (head: its own heading, when nothing above says it)
  function searchView(f, draw, onPick, { castHint = "", head = true, chapters = false } = {}) {
    const input = el("input", { value: f.query, placeholder: "The series' title", spellcheck: "false",
      oninput: (e) => { f.query = e.target.value; },
      onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); runSearch(f, draw); } } });
    const box = el("div", { class: "fs-box" }, head ? el("span", { class: "field-head" }, "Find the series") : null);
    if (f.pick) {
      const { source, item } = f.pick;
      const cast = f.cast && f.cast.length ? `${plural(f.cast.length, "character")} found${castHint}` : f.castStatus;
      box.append(el("div", { class: "fs-picked" },
        item.cover ? el("img", { src: item.cover, alt: "" }) : el("div", { class: "src-nocover" }),
        el("div", {}, el("b", {}, item.title),
          el("span", { class: "muted" }, [`From ${source.name}`, item.year, item.kind].filter(Boolean).join(" · ")),
          item.summary ? el("span", { class: "fs-summary muted" }, item.summary) : null,
          cast ? el("span", { class: "hint" }, cast) : null,
          chapters && source.chapters !== false ? el("label", { class: "check fs-get" },
            el("input", { type: "checkbox", checked: f.getChapters, onchange: (e) => {
              f.getChapters = e.target.checked;
              try { localStorage.setItem(GET, e.target.checked ? "yes" : "no"); } catch { /* (kept for this time only) */ }
            } }), `Get its chapters from ${source.name} once the project is made`) : null,
          el("button", { type: "button", class: "ghost small", onclick: () => { f.pick = null; f.cast = null; draw(); } },
            "Pick another"))));
      return box;
    }
    box.append(el("div", { class: "key-row" }, input,
      el("button", { type: "button", class: "ghost small", onclick: () => runSearch(f, draw) }, "Search")));
    if (f.status) box.append(el("span", { class: "hint" }, f.status));
    for (const r of f.results || []) {
      if (!r.items.length && !r.error) continue;
      box.append(el("span", { class: "fs-source muted" }, r.source.name + (r.source.characters ? " · with characters" : "")));
      box.append(r.error ? el("span", { class: "error" }, r.error) : el("div", { class: "fs-results" }, ...r.items.map((it) =>
        el("button", { type: "button", class: "src-card", title: it.summary || it.title, onclick: () => onPick(r.source, it) },
          it.cover ? el("img", { src: it.cover, alt: "", loading: "lazy" }) : el("div", { class: "src-nocover" }),
          el("span", {}, it.title), el("small", { class: "muted" }, [it.year, it.kind].filter(Boolean).join(" · "))))));
    }
    setTimeout(() => { if (input.isConnected && !f.results) input.focus(); }, 0);
    return box;
  }

  // the picked series' characters, to tick (have: names already in the project; note: a line under the heading)
  function castView(f, draw, have = new Set(), note = "") {
    const box = el("div", { class: "fs-box" });
    if (!f.pick) return box;
    box.append(el("span", { class: "field-head" }, `Characters from ${f.pick.source.name}`));
    if (f.castStatus) box.append(el("span", { class: "hint" }, f.castStatus));
    if (!f.cast || !f.cast.length) return box;
    if (note) box.append(el("span", { class: "hint" }, note));
    const count = el("span", {});
    const recount = () => {
      const on = f.cast.filter((c) => c.on && !have.has(key(c.name))).length;
      count.textContent = `${plural(f.cast.length, "character")} · adding ${on}`;
      box.dispatchEvent(new CustomEvent("fs-count", { detail: on, bubbles: true }));
    };
    const all = (test) => () => { f.cast.forEach((c) => { c.on = test(c); }); draw(); };
    box.append(el("div", { class: "fs-cast-head" }, count,
      el("button", { type: "button", class: "ghost small", onclick: all((c) => c.role !== "background") }, "Main and supporting"),
      el("button", { type: "button", class: "ghost small", onclick: all(() => true) }, "All"),
      el("button", { type: "button", class: "ghost small", onclick: all(() => false) }, "None")),
    el("div", { class: "fs-cast" }, ...f.cast.map((c) => {
      const there = have.has(key(c.name));
      return el("label", { class: "fs-char" + (there ? " there" : "") },
        el("input", { type: "checkbox", checked: c.on && !there, disabled: there,
          onchange: (e) => { c.on = e.target.checked; recount(); } }),
        c.picture ? el("img", { src: c.picture, alt: "", loading: "lazy" }) : el("div", { class: "fs-nopic" }),
        el("div", {}, el("b", {}, c.name), c.role ? el("span", { class: "tag" }, c.role) : null,
          there ? el("span", { class: "tag" }, "in the list") : null,
          c.aliases.length ? el("div", { class: "muted fs-desc" }, `Also: ${c.aliases.join(", ")}`) : null,
          c.description ? el("div", { class: "muted fs-desc" }, c.description) : null));
    })));
    setTimeout(recount, 0);
    return box;
  }

  // Add the ticked characters to a project (those it doesn't have by name), then their pictures. Each is marked with
  // its source, so the character scan still runs in a new project and matches its groups to them.
  async function addCharacters(pid, chosen, source, status = () => {}) {
    const project = await api(`/api/projects/${pid}`);
    const have = new Set(project.characters.map((c) => key(c.name)));
    const adding = chosen.filter((c) => !have.has(key(c.name)));
    if (!adding.length) return 0;
    status(`Adding ${plural(adding.length, "character")}…`);
    const fresh = adding.map((c) => ({ name: c.name, gender: c.gender, aliases: c.aliases.join(", "),
      description: c.description, source }));
    const saved = await api(`/api/projects/${pid}/characters`, { method: "PUT",
      json: { characters: [...project.characters, ...fresh] } });
    const ids = new Map(saved.characters.map((c) => [key(c.name), c.id]));
    let done = 0;
    for (const c of adding.filter((x) => x.picture && ids.has(key(x.name)))) {
      status(`Adding their pictures… ${++done}`);
      try {
        const r = await fetch(c.picture);
        if (!r.ok) continue;
        const fd = new FormData();
        fd.append("files", await r.blob(), "picture.jpg");
        await fetch(`/api/projects/${pid}/characters/${ids.get(key(c.name))}/pictures`, { method: "POST", body: fd });
      } catch (e) { console.error(e); }  // a picture that won't come is left out
    }
    return adding.length;
  }

  // ---- New project: the search on its first page, the characters on its Characters page ----

  let nf = finder(), holder = null, page = "", state = null;
  const drawNew = () => {  // (into the page being drawn: the dialog shows it right after asking the plugins)
    if (!holder) return;
    holder.replaceChildren(page === "comic"
      ? searchView(nf, drawNew, (source, item) => {
        pick(nf, source, item, drawNew);
        state.setName(item.title);
        if (item.kind) state.setKind(item.kind);
      }, { castHint: ": tick who to add on the Characters page", chapters: true })
      : castView(nf, drawNew, new Set(), "The character scan below still runs: it matches the characters it finds in "
        + "the chapters to these, and suggests names for the rest."));
  };

  // The sidebar's box searches: what's typed there is searched for as New project opens.
  R.setNewProjectBox({ placeholder: "Search term", button: "Search",
    title: "Search for the series to make a project of (Find the series)" });
  R.on("new-project-open", ({ name, series }) => { nf = finder(name); nf.want = series && series.url ? series : null; });
  R.on("new-project-page", (e) => {
    ({ page, state } = e);
    if (page !== "comic" && !(page === "characters" && nf.pick && (nf.cast || nf.castStatus))) return;
    holder = el("div", {});
    e.body.prepend(holder);
    drawNew();
    if (page === "comic" && nf.want) pickWanted(nf, drawNew);  // the series another plugin opened New project for
    else if (page === "comic" && nf.query && !nf.ran) runSearch(nf, drawNew);  // the name typed before Create
  });

  // The series New project was opened for (by the Trending finder): picked from the source for its site, when one of
  // yours searches it, as if it had been found here; else it's searched for by its name.
  async function pickWanted(f, draw) {
    const want = f.want;
    Object.assign(f, { want: null, ran: true, status: "Getting the series…" });
    draw();
    let host = "", source = null;
    try { host = new URL(want.url).hostname.replace(/^www\./, ""); } catch { /* not a link: searched for instead */ }
    try {
      source = host && (await api("/api/sources")).sources.find((s) => s.kind === "plugin" && s.search
        && s.sites.some((site) => host === site || host.endsWith("." + site)));
    } catch { /* searched for instead */ }
    f.status = "";
    if (!source) return runSearch(f, draw);
    pick(f, source, { ref: want.url, url: want.url, title: want.title, cover: want.cover, summary: want.summary || "",
      kind: want.kind || "", year: want.year || null }, draw);
  }
  R.on("project-created", async ({ project }) => {
    const chosen = nf.pick && nf.cast ? nf.cast.filter((c) => c.on) : [];
    const source = nf.pick ? nf.pick.source.name : "";
    const chapters = nf.pick && nf.pick.source.chapters !== false && nf.getChapters ? nf.pick : null;
    nf = finder();
    if (chosen.length) await addCharacters(project.id, chosen, source);
    if (chapters) getAll(project.id, chapters.source, chapters.item);  // (not waited for: the project opens meanwhile)
  });

  // Every chapter of the series picked, got into the new project (a Getting chapters job, placed by their numbers)
  async function getAll(pid, source, item) {
    let series;
    try { series = await api("/api/sources/series", { method: "POST", json: { key: source.key, ref: item.ref } }); }
    catch (e) {
      if (!e.check) R.notify(`${e.message}. Chapters → Get chapters from a site can try again.`, "Its chapters weren't got");
      return;  // (a site that wants its check: Site check offers its window, and Get chapters can try again after)
    }
    if (!series.chapters.length) return R.notify(`${source.name} lists no chapters for ${series.title}.`, "No chapters");
    try {
      await api(`/api/projects/${pid}/download`, { method: "POST", json: { key: source.key, place: "number", skip: [],
        series: { ref: series.ref, url: series.url, title: series.title }, chapters: series.chapters } });
    } catch (e) { return R.notify(e.message, "Its chapters weren't got"); }
    const open = R.current();
    if (open && open.id === pid) R.reload();  // (its job shows, with its progress)
  }

  // ---- the Characters tab: the same for a project that already exists ----

  const dlg = el("dialog", { class: "wide-dialog" });
  document.body.append(dlg);
  let cf = finder(), have = new Set();
  const body = el("div", { class: "setup-body" });
  const note = el("span", { class: "muted fs-note" });
  const go = el("button", { type: "button", class: "primary", disabled: true }, "Add characters");
  dlg.append(el("form", { method: "dialog", class: "import-form" },
    el("div", { class: "setup-head" }, el("h2", {}, "Find the series")),
    body,
    el("div", { class: "import-actions" }, note, el("span", { class: "grow" }),
      el("button", { value: "cancel", class: "ghost" }, "Close"), go)));
  body.addEventListener("fs-count", (e) => {
    go.disabled = !e.detail;
    go.textContent = e.detail ? `Add ${plural(e.detail, "character")}` : "Add characters";
  });
  const drawTab = () => {
    go.disabled = true;
    go.textContent = "Add characters";
    body.replaceChildren(searchView(cf, drawTab, (source, item) => pick(cf, source, item, drawTab), { head: false }),
      castView(cf, drawTab, have));
  };
  go.addEventListener("click", async () => {
    const p = R.current();
    go.disabled = true;
    try {
      const n = await addCharacters(p.id, cf.cast.filter((c) => c.on && !have.has(key(c.name))), cf.pick.source.name,
        (s) => { note.textContent = s; });
      note.textContent = "";
      dlg.close();
      await R.reload();
      R.showTab("characters");
      if (n) R.notify(`${plural(n, "character")} added from ${cf.pick.source.name}. Look over their descriptions: ` +
        "they're the site's, and the narrator goes by them.", "Characters added");
    } catch (e) { note.textContent = e.message; go.disabled = false; }
  });
  R.addButton("characters", "Find the series…", () => {
    const p = R.current();
    if (!p) return;
    have = new Set(p.characters.map((c) => key(c.name)));
    if (!cf.pick || cf.forProject !== p.id) { cf = finder(p.name); cf.forProject = p.id; }
    note.textContent = "";
    drawTab();
    dlg.showModal();
    if (!cf.ran) runSearch(cf, drawTab);
  }, { title: "Search for the series, and add its characters with their pictures" });
})();
