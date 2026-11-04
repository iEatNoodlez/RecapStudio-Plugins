// Find the series, a Recap Studio feature plugin (a page script only). New project gets a search box that asks every
// source that can search (AniList from Recap Studio's plugin list, and any source plugin of your own); the series
// picked names the project and suggests what kind of comic it is, and its characters can be ticked on the dialog's
// Characters page and added with their pictures. A series picked from a source with chapters (a source plugin of
// yours for its site) gets them too, once the project is made, unless that's unticked; its characters can come from
// AniList (or another source with characters) with Get characters from AniList. The Characters tab gets the same
// characters for a project that already exists.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el, plural } = R;
  const TICKED = 15;  // main characters, then supporting ones up to this many, start ticked
  const MATCHES = 6;  // series offered when a source with characters has none called the same as the one picked
  const GET = "recap-find-series-chapters";  // whether "Get its chapters" was last left ticked, in this window's storage
  const getChosen = () => { try { return localStorage.getItem(GET) !== "no"; } catch { return true; } };

  // one search: what was typed, the results by source, the series picked, and where its characters come from (the
  // series picked, or the same series on a source with characters) with the series offered there to pick from
  const finder = (query = "") => ({ query, results: null, status: "", ran: false, pick: null, cast: null, castStatus: "",
    castFrom: null, castMatches: null, charSources: [], getChapters: getChosen() });
  const key = (name) => name.trim().toLowerCase();
  const sameTitle = (t) => String(t || "").toLowerCase().normalize("NFKC").replace(/[^\p{L}\p{N}]+/gu, "");
  const withCharacters = (sources) => sources.filter((s) => s.kind === "plugin" && s.search && s.characters);

  // Every source that can search, asked at once, each one's results shown as soon as it answers (the ones with
  // characters first, the rest as they came)
  async function runSearch(f, draw) {
    const q = f.query.trim();
    if (!q) return;
    const run = f.run = (f.run || 0) + 1;  // (a newer search drops this one's late answers)
    Object.assign(f, { ran: true, status: "Searching…", results: null });
    draw();
    let sources;
    try { sources = (await api("/api/sources")).sources.filter((s) => s.search && s.kind === "plugin"); }
    catch (e) { f.status = e.message; return draw(); }
    if (f.run !== run) return;
    f.charSources = withCharacters(sources);
    if (!sources.length) {
      f.status = "None of your sources can search: install AniList from Recap Studio's plugins (Settings → Plugins).";
      return draw();
    }
    f.results = [];
    const got = (source, items, error) => {
      if (f.run !== run || !source) return;
      f.results.push({ source, items: items.slice(0, 8), error });
      f.results.sort((a, b) => (b.source.characters ? 1 : 0) - (a.source.characters ? 1 : 0));
      f.status = f.results.length < sources.length
        ? `Searching… ${f.results.length} of ${plural(sources.length, "source")} answered`
        : f.results.some((r) => r.items.length) ? "" : `Nothing found for "${q}".`;
      draw();
    };
    f.status = `Searching ${plural(sources.length, "source")}…`;
    draw();
    if (R.searchSources) {  // (Recap Studio 0.2.6 and later: one request, each source's answer as it comes)
      const byKey = new Map(sources.map((s) => [s.key, s]));
      try { await R.searchSources(sources.map((s) => s.key), q, (one) => got(byKey.get(one.key), one.items || [], one.error)); }
      catch (e) { if (f.run === run) { f.status = e.message; draw(); } }
      return;
    }
    const left = [...sources];  // (before: each source asked on its own, four at a time)
    await Promise.all(Array.from({ length: Math.min(4, left.length) }, async () => {
      for (let s; (s = left.shift());) {
        try { got(s, (await api("/api/sources/search", { method: "POST", json: { key: s.key, query: q, page: 1 } })).items); }
        catch (e) { got(s, [], e.message); }
      }
    }));
  }

  async function pick(f, source, item, draw) {
    Object.assign(f, { pick: { source, item }, cast: null, castStatus: "", castFrom: null, castMatches: null, castBusy: false });
    if (source.characters) return castOf(f, source, item, draw);
    draw();
  }

  // The characters of a series a source with characters lists: the one picked, or the same series found there
  async function castOf(f, source, item, draw) {
    const picked = f.pick;
    Object.assign(f, { castFrom: { source, item }, cast: null, castMatches: null,
      castStatus: `Getting its characters from ${source.name}…` });
    draw();
    try {
      const found = (await api("/api/sources/characters", { method: "POST", json: { key: source.key, ref: item.ref } })).characters;
      if (f.pick !== picked) return;  // (another series was picked meanwhile)
      let ticked = 0;
      f.cast = found.map((c) => {
        const on = c.role === "main" || (c.role === "supporting" && ticked < TICKED);
        if (on) ticked++;
        return { ...c, on };
      });
      f.castStatus = found.length ? "" : `${source.name} lists no characters for it.`;
    } catch (e) { if (f.pick === picked) f.castStatus = e.message; }
    draw();
  }

  // Get characters from AniList, for a series picked from a source without characters: the same series looked for
  // there by its title. The one called the same is taken (by its year too, when several are); else the closest few
  // are offered to pick from (choose: offered anyway, after "Not this one").
  async function castElsewhere(f, source, draw, { choose = false } = {}) {
    const picked = f.pick, { title, year } = picked.item;
    Object.assign(f, { castFrom: null, cast: null, castMatches: null, castBusy: true,
      castStatus: `Looking for it on ${source.name}…` });
    draw();
    let items;
    try { items = (await api("/api/sources/search", { method: "POST", json: { key: source.key, query: title, page: 1 } })).items; }
    catch (e) { if (f.pick === picked) Object.assign(f, { castBusy: false, castStatus: e.message }); return draw(); }
    if (f.pick !== picked) return;
    f.castBusy = false;
    let same = items.filter((it) => sameTitle(it.title) === sameTitle(title));
    if (same.length > 1 && year) same = same.filter((it) => it.year === year);
    if (same.length === 1 && !choose) return castOf(f, source, same[0], draw);
    if (!items.length) { f.castStatus = `${source.name} has nothing called ${title}.`; return draw(); }
    f.castMatches = { source, items: items.slice(0, MATCHES) };
    f.castStatus = `Which one is it on ${source.name}?`;
    draw();
  }

  // Under the series picked: where its characters come from, or the buttons to get them from a source that has them
  function castChoice(f, draw) {
    const { source } = f.pick, from = f.castFrom;
    if (from && from.source !== source) {
      return el("span", { class: "fs-from" }, el("span", { class: "muted" }, `Characters from ${from.source.name}: ${from.item.title}`),
        el("button", { type: "button", class: "ghost small", onclick: () => castElsewhere(f, from.source, draw, { choose: true }) },
          "Not this one"));
    }
    if (f.castMatches) {
      const { source: there, items } = f.castMatches;
      return el("div", { class: "fs-results fs-matches" }, ...items.map((it) =>
        el("button", { type: "button", class: "src-card", title: it.summary || it.title, onclick: () => castOf(f, there, it, draw) },
          it.cover ? el("img", { src: it.cover, alt: "", loading: "lazy" }) : el("div", { class: "src-nocover" }),
          el("span", {}, it.title), el("small", { class: "muted" }, [it.year, it.kind].filter(Boolean).join(" · ")))));
    }
    if (source.characters || from || f.castBusy || !f.charSources.length) return null;
    return el("span", { class: "fs-from" }, ...f.charSources.map((s) =>
      el("button", { type: "button", class: "ghost small", title: `Find this series on ${s.name} and get the characters it lists`,
        onclick: () => castElsewhere(f, s, draw) }, `Get characters from ${s.name}`)));
  }

  // the search box and its results, or the series picked (head: its own heading, when nothing above says it)
  function searchView(f, draw, onPick, { castHint = "", head = true, chapters = false } = {}) {
    const was = document.activeElement, typing = was && was.classList.contains("fs-query") ? was.selectionStart : null;
    const input = el("input", { class: "fs-query", value: f.query, placeholder: "The series' title", spellcheck: "false",
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
          castChoice(f, draw),
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
    setTimeout(() => {  // (the box keeps its cursor while results come in and redraw it)
      if (!input.isConnected || (typing === null && f.results)) return;
      input.focus();
      if (typing !== null) input.setSelectionRange(typing, typing);
    }, 0);
    return box;
  }

  // the picked series' characters, to tick (have: names already in the project; note: a line under the heading)
  function castView(f, draw, have = new Set(), note = "") {
    const box = el("div", { class: "fs-box" });
    if (!f.pick) return box;
    box.append(el("span", { class: "field-head" }, `Characters from ${(f.castFrom || f.pick).source.name}`));
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
      const all = (await api("/api/sources")).sources;
      f.charSources = withCharacters(all);
      source = host && all.find((s) => s.kind === "plugin" && s.search
        && s.sites.some((site) => host === site || host.endsWith("." + site)));
    } catch { /* searched for instead */ }
    f.status = "";
    if (!source) return runSearch(f, draw);
    pick(f, source, { ref: want.url, url: want.url, title: want.title, cover: want.cover, summary: want.summary || "",
      kind: want.kind || "", year: want.year || null }, draw);
  }
  R.on("project-created", async ({ project }) => {
    const chosen = nf.pick && nf.cast ? nf.cast.filter((c) => c.on) : [];
    const source = nf.castFrom ? nf.castFrom.source.name : "";
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
      const from = cf.castFrom.source.name;
      const n = await addCharacters(p.id, cf.cast.filter((c) => c.on && !have.has(key(c.name))), from,
        (s) => { note.textContent = s; });
      note.textContent = "";
      dlg.close();
      await R.reload();
      R.showTab("characters");
      if (n) R.notify(`${plural(n, "character")} added from ${from}. Look over their descriptions: ` +
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
