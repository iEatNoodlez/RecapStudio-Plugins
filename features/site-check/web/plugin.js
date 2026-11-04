// Site check, a Recap Studio feature plugin (__init__.py): when a site answers your sources with its check for a person
// (Cloudflare's) instead of its page, this offers to open the site in a small window, where you pass the check
// yourself. Recap Studio then uses that pass for the site until it runs out. Settings → Site check lists the passes and
// the sites waiting for theirs, and opens any site's check ahead of time.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el } = R;
  const BASE = "/api/plugins/site-check";
  const offered = new Map();  // host: when it was last offered (not again for a minute), or "no" once declined
  let queue = Promise.resolve(), busy = false, box = null;

  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
  const time = (t) => new Date(t * 1000).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const STUBBORN = "still refuses Recap Studio after its check was passed. It looks at more than the pass (how the "
    + "connection itself looks, which only a real browser has), so a window can't help with this site.";

  // one offer at a time (searching all your sources can meet several sites that ask)
  R.on("site-check", (c) => { queue = queue.then(() => offer(c)).catch(console.error); });

  async function offer(c) {
    const last = offered.get(c.host);
    if (busy || last === "no" || last && Date.now() - last < 60000) return;
    offered.set(c.host, Date.now());
    let st;
    try { st = await api(`${BASE}/status`); } catch { return; }
    const name = c.source ? `${c.source} (${c.host})` : c.host;
    const waiting = st.waiting.find((w) => w.host === c.host);
    if (waiting && waiting.stubborn) return R.notify(`${c.host} ${STUBBORN}`, "Site check");
    if (!st.can_open) return R.notify(`${name} wants to check you're a person first. ${st.why}.`, "Site check");
    const ok = await R.ask(c.refused ? `Pass ${c.host}'s check again?` : `${c.host} wants to check you're a person`,
      (c.refused ? `${name} didn't take the pass from before: it ran out early, or the site wants a new one. `
        : `${name} answered with Cloudflare's check instead of its page. `)
      + "Open the site in a small window and pass the check yourself: wait while it runs, or tick its box. The window "
      + "closes once you're through, and Recap Studio uses that pass for this site until it runs out. Then try again.",
      { ok: "Open the site" });
    if (!ok) return offered.set(c.host, "no");
    await openSite(c.url || `https://${c.host}/`);
  }

  // the window, until it's passed, closed or given up on
  async function openSite(url) {
    let st;
    try { st = await api(`${BASE}/open`, { method: "POST", json: { url } }); }
    catch (e) { return R.notify(e.message, "Site check"); }
    busy = true;
    try {
      draw(st);
      while (st.window.state === "open") {
        await sleep(1000);
        try { st = await api(`${BASE}/status`); } catch { break; }
      }
    } finally { busy = false; }
    draw(st);
    const w = st.window;
    if (w.state === "passed") R.notify(`${w.message}. Try again: search, open the series or get its chapters as before.`, "Site check");
    else if (w.state !== "open") R.notify(w.message, "Site check");
  }

  // Settings → Site check
  R.addSettings("Site check", async (into) => {
    box = into;
    try { draw(await api(`${BASE}/status`)); }
    catch (e) { box.replaceChildren(el("span", { class: "error" }, e.message)); }
  });

  function draw(st) {
    if (!box || !box.isConnected) return;
    const field = el("input", { type: "url", placeholder: "https://example.com/", spellcheck: "false" });
    const go = () => { if (field.value.trim()) openSite(field.value.trim()); };
    field.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
    const open = st.window.state === "open";
    box.replaceChildren(...[
      el("span", { class: "hint" }, "When a site answers your sources with Cloudflare's check for a person, pass it "
        + "yourself in a small window: Recap Studio then gets that site's chapters with your pass until it runs out "
        + "(often 30 minutes, sometimes days: the site decides). Only ever when you open the window."),
      st.can_open ? null : el("span", { class: "error" }, `${st.why}.`),
      open ? el("span", { class: "muted" }, `${st.window.message}…`) : null,
      el("b", {}, "Passes"),
      st.passes.length ? el("div", { class: "src-plugins" }, ...st.passes.map((p) => el("div", { class: "src-plugin" },
        el("b", {}, p.domain),
        el("span", { class: "muted" }, `until ${time(p.expires)}${p.clearance ? "" : " · the site gave no pass of its own, only its cookies"}`),
        el("button", { type: "button", class: "ghost small", onclick: async () => {
          try { draw(await api(`${BASE}/passes/${encodeURIComponent(p.domain)}`, { method: "DELETE" })); }
          catch (e) { R.notify(e.message); }
        } }, "Forget"))))
        : el("span", { class: "muted" }, "None yet."),
      st.waiting.length ? el("b", {}, "Sites that asked") : null,
      st.waiting.length ? el("div", { class: "src-plugins" }, ...st.waiting.map((w) => el("div", { class: "src-plugin" },
        el("b", {}, w.host),
        el("span", { class: w.stubborn ? "error" : "muted" }, w.stubborn ? STUBBORN
          : [w.source, `asked ${time(w.at)}`, w.refused ? "didn't take its pass" : ""].filter(Boolean).join(" · ")),
        el("span", {},
          w.stubborn ? null : el("button", { type: "button", class: "ghost small", disabled: !st.can_open || open,
            onclick: () => openSite(w.url || `https://${w.host}/`) }, "Open the site"),
          el("button", { type: "button", class: "ghost small", title: "Take it off this list", onclick: async () => {
            try { draw(await api(`${BASE}/waiting/${encodeURIComponent(w.host)}`, { method: "DELETE" })); }
            catch (e) { R.notify(e.message); }
          } }, "×"))))) : null,
      st.can_open ? el("b", {}, "Pass a site's check now") : null,
      st.can_open ? el("div", { class: "src-row" }, field,
        el("button", { type: "button", class: "ghost small", disabled: open, onclick: go }, "Open the site")) : null,
    ].filter(Boolean));
  }
})();
