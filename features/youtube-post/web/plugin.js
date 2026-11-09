// Post to YouTube, a Recap Studio feature plugin: Settings → YouTube (cards: your Google OAuth client, the channel,
// what's posted with each video), a Posting section in New project, and Post to YouTube… at Publish. The posting
// itself is __init__.py's.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el } = R;
  const BASE = "/api/plugins/youtube-post";
  const PRIVACY = [["private", "Private", "Only you see it. You make it public in YouTube Studio."],
                   ["unlisted", "Unlisted", "Anyone with the link sees it."],
                   ["public", "Public", "Everyone sees it."]];
  const SHORTS = [["none", "Don't post them", ""], ["all", "Post them with the video", ""],
    ["daily", "Post one a day", "The first at the time below after the video goes up, then one each day. Recap Studio has to be open then (or it posts it the next time it is)."]];
  const AUDIT = "Until your Google project passes YouTube's API audit, YouTube keeps videos posted through it private, " +
    "whatever you pick here.";
  const GUIDE = "https://github.com/iEatNoodlez/RecapStudio-Plugins/wiki/Connecting-your-YouTube-channel";
  const MORE_NEEDED = "Subtitles and playlists need Also allow playlists and subtitles (Settings → YouTube), then connecting again.";
  const card = (title, chip, ...kids) => el("section", { class: "pk-card" }, el("h3", {}, title, chip), ...kids.filter(Boolean));
  const chip = (text, tone = "") => text ? el("span", { class: `side-chip ${tone}` }, text) : null;
  let account = null;

  // choices as pills (the dialog's and Settings' .pk-pills, or New project's own .np-pills)
  const pills = (name, options, value, onpick, cls = "pk-pills") => el("div", { class: cls }, ...options.map(([id, title]) =>
    el("label", { class: `${cls === "np-pills" ? "" : "pk-pill"}${id === value ? " on" : ""}` },
      el("input", { type: "radio", name, checked: id === value, onchange: () => onpick(id) }), title)));
  const about = (options, value) => { const o = options.find(([id]) => id === value); return o && o[2] ? el("span", { class: "hint" }, o[2]) : null; };

  // The Shorts choice, the same in New project and the dialog (portrait: whether the project's Shorts are Shorts);
  // without its heading, which each place adds its own way.
  function shortsChoice(c, portrait, makesShorts, draw, cls) {
    if (!portrait) {
      return [el("span", { class: "hint" }, "This project's video is landscape, so its Shorts would post as ordinary " +
        "videos. Portrait projects post them as Shorts.")];
    }
    const time = el("input", { type: "time", value: c.daily_at, disabled: c.shorts !== "daily",
      oninput: (e) => { c.daily_at = e.target.value; } });
    return [
      makesShorts ? null : el("span", { class: "hint" }, "Only if the project makes Shorts (the Video stage's panel, Captions and Shorts: Cut into Shorts)."),
      pills("yt-shorts", SHORTS, c.shorts, (v) => { c.shorts = v; draw(); }, cls),
      about(SHORTS, c.shorts),
      c.shorts === "daily" ? el("label", { class: "pk-line" }, "At", time) : null,
    ].filter(Boolean);
  }

  // Subtitles and the playlist, the same in New project and the dialog (lists: the channel's playlists, or null).
  function extras(c, lists, draw) {
    const pick = el("select", { onchange: (e) => { c.playlist = e.target.value; draw(); } },
      el("option", { value: "" }, "No playlist"),
      el("option", { value: "new" }, "One named after the series (made the first time)"),
      ...(lists || []).map((x) => el("option", { value: x.id }, x.title)));
    pick.value = c.playlist || "";
    if (pick.value !== (c.playlist || "")) pick.append(el("option", { value: c.playlist, selected: true }, "The playlist chosen before"));
    return [
      el("label", { class: "check" }, el("input", { type: "checkbox", checked: !!c.subtitles,
        onchange: (e) => { c.subtitles = e.target.checked; draw(); } }), " Upload its subtitles (its narration, word for word, as English captions)"),
      el("label", { class: "pk-line" }, "Playlist", pick),
      c.playlist ? el("label", { class: "check" }, el("input", { type: "checkbox", checked: !!c.playlist_shorts,
        onchange: (e) => { c.playlist_shorts = e.target.checked; } }), " Its Shorts too") : null,
      (c.subtitles || c.playlist) && account && !account.more ? el("span", { class: "hint warn" }, MORE_NEEDED) : null,
    ].filter(Boolean);
  }

  async function loadAccount() {
    try { account = await api(`${BASE}/account`); } catch { account = null; }
    return account;
  }

  // ---- Settings → YouTube ----

  R.addSettings("YouTube", async (box) => {
    await loadAccount();
    drawSettings(box);
  });

  function drawSettings(box) {
    if (!account) { box.replaceChildren(el("span", { class: "error" }, "Couldn't reach the plugin.")); return; }
    const a = account, error = el("div", { class: "error" });
    const id = el("input", { placeholder: "Client ID (…apps.googleusercontent.com)", spellcheck: "false", autocomplete: "off" });
    const secret = el("input", { type: "password", placeholder: "Client secret", autocomplete: "new-password" });
    const saveClient = async (client_id, client_secret) => {
      try { account = await api(`${BASE}/client`, { method: "PUT", json: { client_id, client_secret } }); drawSettings(box); }
      catch (e) { error.textContent = e.message; }
    };
    const file = el("input", { type: "file", accept: ".json", hidden: true, onchange: async (e) => {
      const f = e.target.files[0];
      e.target.value = "";
      try {
        const j = JSON.parse(await f.text()), c = j.installed || j.web || {};
        await saveClient(c.client_id || "", c.client_secret || "");
      } catch { error.textContent = "That file isn't a Google OAuth client's JSON."; }
    } });
    const connect = async () => {
      let r;
      try { r = await api(`${BASE}/connect`, { method: "POST" }); } catch (e) { error.textContent = e.message; return; }
      if (r.server && r.url) window.open(r.url, "_blank", "noopener");  // (a server: in this browser, not the server's)
      const wait = async () => {
        await loadAccount();
        drawSettings(box);
        if (account && account.connect.state === "waiting") setTimeout(wait, 2000);
      };
      wait();
    };
    // a server: Google sends the browser to an address on this computer (127.0.0.1) that it can't open; pasted here,
    // it finishes the sign-in on the server
    const pasteBox = () => {
      const address = el("input", { placeholder: "http://127.0.0.1:…/oauth?state=…&code=…", spellcheck: "false" });
      const send = el("button", { type: "button", class: "ghost small", onclick: async () => {
        try { await api(`${BASE}/connect/paste`, { method: "POST", json: { address: address.value } }); }
        catch (e) { error.textContent = e.message; }
        await loadAccount();
        drawSettings(box);
      } }, "Finish connecting");
      return el("div", { class: "pk-card" }, el("span", { class: "hint" }, "Recap Studio runs on a server here, so once you allow it, your " +
        "browser goes to an address starting with http://127.0.0.1 that doesn't open. Copy that whole address from the " +
        "address bar and paste it here:"), el("div", { class: "pk-row" }, address, send));
    };
    const tpl = (key, label, rows) => {
      const t = el("textarea", { rows });
      t.value = a.settings[key];
      t.addEventListener("change", async () => {
        account = await api(`${BASE}/settings`, { method: "PUT", json: { settings: { [key]: t.value } } });
      });
      return el("label", { class: "pk-field" }, label, t);
    };
    const category = el("select", { onchange: async (e) => {
      account = await api(`${BASE}/settings`, { method: "PUT", json: { settings: { category: e.target.value } } });
    } }, ...[["1", "Film & Animation"], ["24", "Entertainment"], ["27", "Education"], ["22", "People & Blogs"]]
      .map(([v, t]) => el("option", { value: v }, t)));
    category.value = a.settings.category;
    const tags = el("input", { value: a.settings.tags, placeholder: "manga recap, manhwa, …", onchange: async (e) => {
      account = await api(`${BASE}/settings`, { method: "PUT", json: { settings: { tags: e.target.value } } });
    } });
    const waiting = a.connect.state === "waiting";
    const tone = a.channel ? "ok" : waiting ? "on" : a.connect.state === "error" ? "bad" : "";
    box.replaceChildren(...[
      el("span", { class: "hint" }, "Posts a project's video and Shorts to your channel through YouTube's own API, from Publish " +
        "(Post to YouTube…) or each time a project's video is made, when it says so."),
      card("Your Google OAuth client", chip(a.client ? "set" : "needed", a.client ? "ok" : "warn"),
        el("span", { class: "hint" }, "Google lets a program post to your channel only through an OAuth client of your own, made once, for free:"),
        el("ol", { class: "pk-steps" },
          el("li", {}, "In Google Cloud Console (console.cloud.google.com), make a project and turn on the YouTube Data API v3."),
          el("li", {}, "Set up its sign-in (Google Auth Platform, External), then publish it: Audience → Publish app. "
            + "Left in Testing, Google signs you out every 7 days."),
          el("li", {}, "Make an OAuth client of the type Desktop app, then download its JSON or copy its ID and secret here."),
          el("li", {}, "Connect your channel, below: Google's sign-in page opens in your browser.")),
        el("a", { class: "link yt-guide", href: GUIDE, target: "_blank", rel: "noopener" }, "The walkthrough, step by step (opens in your browser)"),
        a.client ? el("span", { class: "hint" }, `OAuth client: ${a.client_id}`) : null,
        el("div", { class: "pk-row" }, id, secret, el("button", { type: "button", class: "ghost small",
          onclick: () => saveClient(id.value, secret.value) }, a.client ? "Replace" : "Save")),
        el("div", { class: "pk-actions" }, el("label", { class: "button-like ghost small" }, "Add the client's JSON file…", file))),
      card("Your channel", chip(a.channel ? "connected" : waiting ? "waiting" : "", tone),
        el("div", { class: "pk-status" }, el("span", { class: `pk-dot ${tone}` }),
          el("span", {}, a.channel ? `Connected: ${a.channel}.` : waiting ? "Waiting for you to sign in, in your browser…"
            : a.connect.state === "error" ? `Not connected: ${a.connect.error}` : "Not connected.")),
        waiting && a.server ? pasteBox() : null,
        a.blocked_until ? el("span", { class: "hint warn" }, `YouTube's daily limit is used up: posting carries on after ${a.blocked_until}.`) : null,
        el("div", { class: "pk-actions" },
          el("button", { type: "button", class: "ghost small", disabled: !a.client || waiting, onclick: connect },
            a.channel ? "Connect another channel" : "Connect your channel"),
          a.channel ? el("button", { type: "button", class: "ghost small danger", onclick: async () => {
            if (!await R.ask("Disconnect the channel?", "Recap Studio's access to it is taken back; nothing on YouTube changes.", { ok: "Disconnect" })) return;
            account = await api(`${BASE}/disconnect`, { method: "POST" });
            drawSettings(box);
          } }, "Disconnect") : null),
        el("label", { class: "check" }, el("input", { type: "checkbox", checked: !!a.settings.more, onchange: async (e) => {
          account = await api(`${BASE}/settings`, { method: "PUT", json: { settings: { more: e.target.checked ? "yes" : "" } } });
          drawSettings(box);
        } }), " Also allow playlists and subtitles"),
        el("span", { class: a.settings.more && a.channel && !a.more ? "hint warn" : "hint" },
          a.settings.more && a.channel && !a.more ? "Connect again (above) to allow them: Google asks you once more."
            : "They need a wider permission: managing your videos, playlists and captions. Google asks for it when you connect.")),
      error,
      card("What's posted with each video", null,
        el("span", { class: "hint warn" }, AUDIT + " YouTube also allows a Google project about 6 uploads a day: the rest go up the next day."),
        tpl("description", "Description ({title}, {series}, {chapters}: its YouTube chapter list, {part})", 3),
        tpl("shorts_description", "A Short's description", 2),
        el("div", { class: "pk-fields" }, el("label", { class: "pk-field" }, "Category", category),
          el("label", { class: "pk-field" }, "Tags (comma-separated)", tags)))].filter(Boolean));
  }

  // ---- New project: a Posting section ----

  let nc = null;  // the new project's choices
  const fresh = () => ({ when: "off", privacy: "private", shorts: "none", daily_at: "18:00", subtitles: false, playlist: "",
                         playlist_shorts: false });
  R.on("new-project-open", () => { nc = fresh(); loadAccount(); });
  R.addNewProjectPage({ id: "youtube-post", title: "Posting to YouTube", render: ({ body, state }) => {
    nc = nc || fresh();
    const s = state.settings || {};
    const draw = () => state.redraw();
    const WHEN = [["off", "Don't post it", "Post to YouTube… at Publish posts it when you say."],
      ["auto", "Post it to YouTube", "As soon as Make the recap (or Render video) finishes it."]];
    body.append(...[
      el("span", { class: "field-head" }, "When its video is made"),
      pills("yt-when", WHEN, nc.when, (v) => { nc.when = v; draw(); }, "np-pills"),
      about(WHEN, nc.when)].filter(Boolean));
    if (nc.when === "auto") {
      body.append(...[
        account && !account.channel ? el("span", { class: "hint warn" }, "No channel is connected yet: Settings → YouTube. " +
          "It posts once one is.") : null,
        account && account.channel ? el("span", { class: "hint" }, `To ${account.channel}.`) : null,
        el("span", { class: "field-head" }, "Who sees it"),
        pills("yt-privacy", PRIVACY, nc.privacy, (v) => { nc.privacy = v; draw(); }, "np-pills"),
        about(PRIVACY, nc.privacy),
        el("span", { class: "hint" }, AUDIT),
        s.youtube_safe ? el("span", { class: "hint warn" }, "A YouTube-safe project is for an age-restricted upload: post it as " +
          "Private, set the age restriction in YouTube Studio, then publish it.") : null,
        el("span", { class: "field-head" }, "Its Shorts"),
        ...shortsChoice(nc, s.orientation === "portrait", !!Number(s.shorts), draw, "np-pills"),
        el("span", { class: "field-head" }, "Also"),
        ...extras(nc, null, draw)].filter(Boolean));
    }
  } });
  R.on("project-created", async ({ project }) => {
    const choice = nc;
    nc = null;
    if (choice && choice.when === "auto") {
      await api(`${BASE}/projects/${project.id}`, { method: "PUT", json: { choice } });
    }
  });

  // ---- Publish: Post to YouTube… ----

  const dlg = el("dialog", { class: "wide-dialog yt-dialog" });
  const body = el("div", { class: "setup-body" }), actions = el("div", { class: "import-actions" });
  dlg.append(el("form", { method: "dialog", class: "import-form", novalidate: true },
    el("div", { class: "setup-head" }, el("h2", {}, "Post to YouTube")), body, actions));
  document.body.append(dlg);
  let st = null, c = null, again = false, lists = null;
  const STATE = { posted: "Posted", changed: "Made again since it was posted", new: "Not posted yet",
                  due: "Goes up", waiting: "After the video" };
  const stateTone = (it) => it.state === "posted" ? "ok" : it.state === "changed" || it.state === "due" ? "warn" : "";

  // at Publish (apps from 0.4.3), else with the video's buttons
  R.addButton(R.places && R.places.includes("publish") ? "publish" : "video", "Post to YouTube…", async () => {
    const p = R.current();
    if (!p) return;
    try { st = await api(`${BASE}/projects/${p.id}`); } catch (e) { return R.notify(e.message); }
    c = { ...st.choice };
    again = false;
    await loadAccount();
    drawPost();
    dlg.showModal();
    if (st.channel && lists === null) {  // the channel's playlists, for the choice
      try { lists = (await api(`${BASE}/playlists`)).playlists; } catch { lists = []; }
      if (dlg.open) drawPost();
    }
  }, { title: "Post this project's video (and its Shorts) to your YouTube channel" });

  function drawPost() {
    const p = R.current(), draw = drawPost;
    const changed = st.items.some((it) => it.state === "changed");
    const title = el("input", { value: c.title, placeholder: st.title, oninput: (e) => { c.title = e.target.value; } });
    const desc = el("textarea", { rows: 3, placeholder: `${st.settings.description} (from Settings → YouTube)`, oninput: (e) => { c.description = e.target.value; } });
    desc.value = c.description;
    const at = el("input", { type: "datetime-local", value: c.publish_at, disabled: c.privacy !== "private",
      oninput: (e) => { c.publish_at = e.target.value; } });
    const item = (it) => el("div", { class: "pk-item" },
      el("span", { class: "pk-main" }, el("span", { class: "tag" }, it.kind === "short" ? "Short" : "Video"), el("span", { class: "yt-name" }, it.title),
        it.subtitles ? el("span", { class: "tag" }, "subtitles") : null, it.playlist ? el("span", { class: "tag" }, "in the playlist") : null),
      el("span", { class: `pk-state ${stateTone(it)}` }, it.url ? el("a", { href: it.url, target: "_blank", rel: "noopener" }, `${STATE[it.state]} ${it.at || ""}`)
        : `${STATE[it.state]}${it.due ? ` ${it.due}` : ""}`));
    body.replaceChildren(el("div", { class: "setup-page" }, ...[
      el("span", { class: st.channel ? "hint" : "hint warn" }, st.channel ? `To ${st.channel}.` : "No channel is connected: Settings → YouTube."),
      st.blocked_until ? el("span", { class: "hint warn" }, `YouTube's daily limit is used up: posting carries on after ${st.blocked_until}.`) : null,
      el("div", { class: "pk-cols" },
        el("div", { class: "pk-col" },
          card("The video", null,
            st.items.length ? el("div", { class: "pk-list" }, ...st.items.map(item))
              : el("span", { class: "pk-empty" }, "No video yet: Make the recap renders it.")),
          card("Title and description", null,
            el("label", { class: "pk-field" }, "Title", title),
            el("label", { class: "pk-field" }, "Description", desc))),
        el("div", { class: "pk-col" },
          card("Who sees it", null,
            pills("yt-privacy2", PRIVACY, c.privacy, (v) => { c.privacy = v; if (v !== "private") c.publish_at = ""; draw(); }),
            about(PRIVACY, c.privacy),
            c.privacy === "private" ? el("label", { class: "pk-line" }, "Make it public at", at, el("span", { class: "muted" }, "(empty: it stays private)")) : null,
            el("span", { class: "hint" }, AUDIT)),
          card("Its Shorts", null, ...shortsChoice(c, st.portrait, !!Number((p.settings || {}).shorts), draw)),
          card("Also", null, ...extras(c, lists, draw),
            el("label", { class: "check" }, el("input", { type: "checkbox", checked: c.when === "auto",
              onchange: (e) => { c.when = e.target.checked ? "auto" : "off"; } }), " Post it by itself each time its video is made"),
            changed ? el("label", { class: "check" }, el("input", { type: "checkbox", checked: again, onchange: (e) => { again = e.target.checked; } }),
              " Also post again what was made again since it was posted") : null)))].filter(Boolean)));
    const save = async () => { st = await api(`${BASE}/projects/${p.id}`, { method: "PUT", json: { choice: c } }); };
    actions.replaceChildren(el("button", { value: "cancel", class: "ghost", formnovalidate: true }, "Close"), el("span", { class: "grow" }),
      el("button", { type: "button", class: "ghost", onclick: async () => {
        try { await save(); dlg.close(); } catch (e) { R.notify(e.message); }
      } }, "Save"),
      el("button", { type: "button", class: "primary", disabled: !st.channel || !st.items.length, onclick: async () => {
        try {
          await save();
          await api(`${BASE}/projects/${p.id}/post`, { method: "POST", json: { again } });
        } catch (e) { return R.notify(e.message, "Not posted"); }
        dlg.close();
        R.reload();
      } }, "Post now"));
  }
})();
