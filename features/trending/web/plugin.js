// Trending finder, a Recap Studio feature plugin: a sidebar button opens the series trending now on AniList, or looked
// at most on MangaUpdates (__init__.py), narrowed by kind, genre and status. The ones a project is already of are
// marked, and Make a project opens New project for one (Find the series then picks it, with its characters). Once the
// project is made, Get chapters from a site searches all your own sources for it (RecapStudio.getChapters), or gets
// its chapters straight away when one has a series called that: Settings → Trending finder says which.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el, plural } = R;
  const BASE = "/api/plugins/trending";
  const KEPT = "recap-trending";  // the list last looked at, in this window's storage
  const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

  let options = null, items = [], more = false, page = 1, note = "", failed = false, picked = null, asked = 0;
  let have = new Map();  // a project's name, as compared (below): the name
  let settings = { chapters: "show" }, searchers = null;  // what's done after Make a project; your sources that search
  let thenHint = null;  // the line under Make a project that says so
  let making = null, made = null;  // the series New project was opened for, and then the project made of it
  const form = { site: "anilist", order: "trending", kind: "all", genre: "", status: "", adult: false };
  try { Object.assign(form, JSON.parse(localStorage.getItem(KEPT) || "{}")); } catch { /* the defaults */ }

  // names compared without case, spaces or punctuation ("Solo Leveling: Ragnarok" and "solo leveling ragnarok")
  const same = (name) => String(name || "").toLowerCase().normalize("NFKC").replace(/[^\p{L}\p{N}]+/gu, "");
  const projectOf = (it) => it.titles.map((t) => have.get(same(t))).find(Boolean) || "";
  const site = () => options.sites.find((s) => s.id === form.site) || options.sites[0];

  R.addButton("sidebar", "Trending…", open, { title: "The series trending now, to pick the next one to recap" });
  const dlg = el("dialog", { class: "wide-dialog tr-dialog" });
  const head = el("span", { class: "muted tr-about" });
  const filters = el("div", { class: "tr-filters" });
  const main = el("div", { class: "tr-main" });
  const foot = el("span", { class: "muted tr-foot" });
  const moreButton = el("button", { type: "button", class: "ghost", onclick: () => load(page + 1) }, "Show more");
  dlg.append(el("form", { method: "dialog", class: "import-form" },
    el("div", { class: "setup-head" }, el("h2", {}, "Trending"), head),
    filters, main,
    el("div", { class: "import-actions" }, foot, el("span", { class: "grow" }), moreButton,
      el("button", { value: "cancel", class: "ghost" }, "Close"))));
  document.body.append(dlg);

  async function open() {
    try {
      options ||= await api(`${BASE}/options`);
      have = new Map((await api("/api/projects")).map((p) => [same(p.name), p.name]));
      settings = await api(`${BASE}/settings`);
    } catch (e) { return R.notify(e.message); }
    countSearchers().then(() => { if (thenHint) thenHint.textContent = then(); });  // (it takes seconds: not waited for)
    fit();
    drawFilters();
    dlg.showModal();
    if (!items.length) load(1); else drawMain();
  }

  // the filters kept to what the site has (MangaUpdates doesn't say which series are finished)
  function fit() {
    const s = site();
    form.site = s.id;
    if (!s.orders.some(([id]) => id === form.order)) form.order = s.orders[0][0];
    if (!s.kinds.some(([id]) => id === form.kind)) form.kind = "all";
    if (!options.genres.includes(form.genre)) form.genre = "";
    if (!s.status) form.status = "";
  }

  function change(key, value) {
    form[key] = value;
    fit();
    try { localStorage.setItem(KEPT, JSON.stringify(form)); } catch { /* not kept */ }
    drawFilters();
    load(1);
  }

  function drawFilters() {
    const s = site();
    const select = (key, list, title) => el("select", { title, onchange: (e) => change(key, e.target.value) },
      ...list.map(([v, text]) => el("option", { value: v, selected: form[key] === v }, text)));
    head.textContent = s.about;
    foot.textContent = `From ${s.name}${s.name.endsWith("s") ? "'" : "'s"} public API.`;
    filters.replaceChildren(...[
      el("div", { class: "src-modes", role: "tablist" }, ...options.sites.map((x) =>
        el("button", { type: "button", role: "tab", class: x.id === s.id ? "on" : "", onclick: () => change("site", x.id) }, x.name))),
      select("order", s.orders, "Which list"),
      select("kind", s.kinds, "What kind of comic"),
      select("genre", [["", "Any genre"], ...options.genres.map((g) => [g, g])], "Genre"),
      s.status ? select("status", [["", "Finished or not"], ["releasing", "Still coming out"], ["finished", "Finished"]],
        "Whether it's finished") : null,
      el("label", { class: "check tr-adult" }, el("input", { type: "checkbox", checked: form.adult,
        onchange: (e) => change("adult", e.target.checked) }), "Adult series too")].filter(Boolean));
  }

  async function load(n) {
    const mine = ++asked;
    if (n === 1) { items = []; picked = null; }
    note = n === 1 ? `Asking ${site().name}…` : "Getting more…";
    failed = false;
    moreButton.hidden = true;
    drawMain();
    const q = new URLSearchParams({ ...form, adult: form.adult ? "true" : "false", page: String(n) });
    try {
      const r = await api(`${BASE}/list?${q}`);
      if (mine !== asked) return;  // (the filters changed meanwhile)
      items = n === 1 ? r.items : [...items, ...r.items];
      page = n;
      more = r.more;
      note = items.length ? "" : "Nothing on this list: try another kind or genre.";
    } catch (e) {
      if (mine !== asked) return;
      note = e.message;
      failed = true;
      more = false;
    }
    moreButton.hidden = !more;
    drawMain();
  }

  function card(it) {
    const mine = projectOf(it);
    return el("button", { type: "button", class: "src-card tr-card" + (picked === it ? " on" : ""), title: it.title,
      onclick: () => { picked = picked === it ? null : it; drawMain(); } },
    el("div", { class: "tr-cover" },
      it.cover ? el("img", { src: it.cover, alt: "", loading: "lazy" }) : el("div", { class: "src-nocover" }),
      el("span", { class: "tr-rank" }, `#${it.rank}`),
      mine ? el("span", { class: "tr-have", title: `Your project: ${mine}` }, "✓ Your project") : null),
    el("span", { class: "tr-title" }, it.title),
    el("small", { class: "muted" }, [it.type, it.year, it.status].filter(Boolean).join(" · ")),
    it.score || it.members ? el("small", { class: "muted" },
      [it.score, it.members ? `${compact.format(it.members)} on lists` : ""].filter(Boolean).join(" · ")) : null);
  }

  function detail(it) {
    const mine = projectOf(it), s = site();
    const others = it.titles.filter((t) => t !== it.title).slice(0, 3);
    const make = R.newProject
      ? el("button", { type: "button", class: "primary", onclick: () => {
        dlg.close();
        making = { url: it.url, titles: searchOrder(it) };
        R.newProject({ name: it.title, kind: it.kind,
          series: { url: it.url, title: it.title, cover: it.cover, summary: it.summary, kind: it.kind, year: it.year } });
      } }, mine ? "Make another project of it" : "Make a project")
      : el("button", { type: "button", class: "primary", onclick: async () => {  // (an app from before RecapStudio.newProject)
        try { await navigator.clipboard.writeText(it.title); } catch { /* shown in the note */ }
        R.notify(`Copied "${it.title}". Paste it into the box at the top left to make a project of it.`, "Trending");
      } }, "Copy its title");
    return el("aside", { class: "tr-detail" },
      el("div", { class: "tr-detail-head" },
        it.cover ? el("img", { src: it.cover, alt: "" }) : el("div", { class: "src-nocover" }),
        el("div", {}, el("b", {}, it.title),
          others.length ? el("span", { class: "muted tr-others" }, others.join(" · ")) : null,
          el("span", { class: "muted" }, `#${it.rank} on ${s.name} · ${s.orders.find(([id]) => id === form.order)[1]}`))),
      el("div", { class: "tr-facts" },
        [it.one_shot ? `${it.type} one-shot` : it.type, it.year, it.status,
          it.chapters ? plural(it.chapters, "chapter") : ""].filter(Boolean).join(" · ")),
      it.score || it.members ? el("div", { class: "tr-facts" }, [it.score ? `Score ${it.score}` : "",
        it.members ? `${it.members.toLocaleString()} members have it on their lists` : ""].filter(Boolean).join(" · ")) : null,
      it.genres.length ? el("div", { class: "tr-genres" }, ...it.genres.map((g) => el("span", {}, g))) : null,
      mine ? el("span", { class: "hint" }, `You have a project of it: ${mine}.`) : null,
      it.summary ? el("p", { class: "tr-summary" }, it.summary) : null,
      el("div", { class: "tr-make" }, el("div", { class: "tr-buttons" }, make,
        it.url ? el("a", { class: "link", href: it.url, target: "_blank", rel: "noopener" }, `Open on ${s.name} ↗`) : null),
      thenHint = R.newProject ? el("span", { class: "hint tr-then" }, then()) : null));
  }

  // how many of your sources can search for chapters (asking for them looks for a Suwayomi server, which can take seconds)
  async function countSearchers() {
    try { searchers = (await api("/api/sources")).sources.filter((s) => s.chapters !== false && s.search).length; }
    catch { searchers = null; }
    return searchers;
  }

  // what Make a project does after, about the chapters
  function then() {
    if (settings.chapters === "off") return "Its chapters aren't looked for (Settings → Trending finder).";
    if (!R.getChapters) return "Then add its chapters with Get chapters from a site or Import folder.";
    if (searchers === 0) return "None of your sources can search for its chapters: add one in Settings → Sources, or import them.";
    return settings.chapters === "get"
      ? "Then it gets every chapter, when one of your sources has a series called that (else it shows what they found)."
      : "Then it looks for its chapters in your sources, to pick from.";
  }

  // its titles to search your sources by: the one shown, then the others in the Latin alphabet, then the rest
  function searchOrder(it) {
    const latin = (t) => /^[\p{Script=Latin}\p{N}\p{P}\p{S}\s]+$/u.test(t);
    const rest = it.titles.filter((t) => t !== it.title);
    return [it.title, ...rest.filter(latin), ...rest.filter((t) => !latin(t))];
  }

  // ---- after Make a project: its chapters, once the project is open ----

  R.on("new-project-open", ({ series }) => {  // (New project opened any other way isn't for the series)
    if (!(making && series && series.url === making.url)) making = null;
  });
  R.on("project-created", ({ project }) => {
    made = making ? { id: project.id, titles: making.titles } : null;
    making = null;
  });
  R.on("project-opened", async ({ project }) => {
    if (!made || made.id !== project.id) return;
    const { titles } = made;
    made = null;
    if (!R.getChapters || settings.chapters === "off") return;
    if (searchers === null) await countSearchers();  // (else as counted when the list was open)
    if (searchers !== 0) setTimeout(() => R.getChapters({ titles, auto: settings.chapters === "get" }), 0);  // (after its tab shows)
  });

  // ---- Settings → Trending finder ----

  R.addSettings("Trending finder", async (box) => {
    try { settings = await api(`${BASE}/settings`); } catch { /* as it was */ }
    const choice = el("select", { onchange: async (e) => {
      try { settings = await api(`${BASE}/settings`, { method: "PUT", json: { chapters: e.target.value } }); }
      catch (err) { R.notify(err.message); }
    } }, ...[["show", "Look for them in my sources, and show what's found"],
      ["get", "Get them all straight away, when one of my sources has a series called that"],
      ["off", "Don't look for them"]].map(([v, text]) => el("option", { value: v, selected: settings.chapters === v }, text)));
    box.replaceChildren(el("label", {}, "After Make a project, its chapters ", choice),
      el("span", { class: "hint" }, "Your sources are the ones in Settings → Sources: a plugin of your own, or a " +
        "Suwayomi server. Recap Studio's plugin list has none, since it gets no chapters itself. All of them that can " +
        "search are asked, by the series' title, then its other titles."));
  });

  function drawMain() {
    main.classList.toggle("picked", !!picked);
    const yours = items.filter((it) => projectOf(it)).length;
    main.replaceChildren(...[
      el("div", { class: "tr-list" },
        note ? el("span", { class: failed ? "error" : "hint" }, note) : null,
        yours ? el("span", { class: "hint" }, `${yours} of these ${yours === 1 ? "is" : "are"} already a project of yours (marked).`) : null,
        el("div", { class: "tr-grid" }, ...items.map(card))),
      picked ? detail(picked) : null].filter(Boolean));
  }
})();
