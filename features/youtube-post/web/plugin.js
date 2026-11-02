// Post to YouTube, a Recap Studio feature plugin: Settings → YouTube (your Google OAuth client, the channel, the
// description templates), a Posting step in New project, and Post to YouTube… in the Video tab. The posting itself is
// __init__.py's.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el, plural } = R;
  const BASE = "/api/plugins/youtube-post";
  const PRIVACY = [["private", "Private", "Only you see it. You make it public in YouTube Studio."],
                   ["unlisted", "Unlisted", "Anyone with the link sees it."],
                   ["public", "Public", "Everyone sees it."]];
  const AUDIT = "Until your Google project passes YouTube's API audit, YouTube keeps videos posted through it private, " +
    "whatever you pick here.";
  const GUIDE = "https://github.com/iEatNoodlez/RecapStudio-Plugins/wiki/Connecting-your-YouTube-channel";
  const MORE_NEEDED = "Subtitles and playlists need Also allow playlists and subtitles (Settings → YouTube), then connecting again.";

  // Subtitles and the playlist, the same in New project and the Video tab (lists: the channel's playlists, or null).
  function extras(c, lists, draw) {
    const pick = el("select", { onchange: (e) => { c.playlist = e.target.value; draw(); } },
      el("option", { value: "" }, "No playlist"),
      el("option", { value: "new" }, "One named after the series (made the first time)"),
      ...(lists || []).map((x) => el("option", { value: x.id }, x.title)));
    pick.value = c.playlist || "";
    if (pick.value !== (c.playlist || "")) pick.append(el("option", { value: c.playlist, selected: true }, "The playlist chosen before"));
    return el("div", { class: "yt-extras" },
      el("span", { class: "field-head" }, "Also"),
      el("label", { class: "check" }, el("input", { type: "checkbox", checked: !!c.subtitles,
        onchange: (e) => { c.subtitles = e.target.checked; draw(); } }), " Upload its subtitles (its narration, word for word, as English captions)"),
      el("label", { class: "check yt-time" }, "Playlist ", pick),
      c.playlist ? el("label", { class: "check" }, el("input", { type: "checkbox", checked: !!c.playlist_shorts,
        onchange: (e) => { c.playlist_shorts = e.target.checked; } }), " Its Shorts too") : null,
      (c.subtitles || c.playlist) && account && !account.more ? el("span", { class: "hint warn" }, MORE_NEEDED) : null);
  }
  let account = null;

  async function loadAccount() {
    try { account = await api(`${BASE}/account`); } catch { account = null; }
    return account;
  }

  const radios = (name, options, value, onpick) => el("div", { class: "setup-choices" }, ...options.map(([id, title, about]) =>
    el("label", { class: `setup-choice${id === value ? " on" : ""}` },
      el("input", { type: "radio", name, checked: id === value, onchange: () => onpick(id) }),
      el("div", {}, el("b", {}, title), about ? el("div", { class: "muted" }, about) : ""))));

  // The Shorts choice, the same in New project and the Video tab (portrait: whether the project's Shorts are Shorts).
  function shortsChoice(c, portrait, makesShorts, draw) {
    if (!portrait) {
      return el("span", { class: "hint" }, "Shorts: this project's video is landscape, so its Shorts would post as ordinary " +
        "videos. Portrait projects post them as Shorts.");
    }
    const time = el("input", { type: "time", value: c.daily_at, disabled: c.shorts !== "daily",
      oninput: (e) => { c.daily_at = e.target.value; } });
    return el("div", {},
      el("span", { class: "field-head" }, "Its Shorts"),
      makesShorts ? null : el("span", { class: "hint" }, "Only if the project makes Shorts (How its video looks: Also cut the video into Shorts)."),
      radios("yt-shorts", [["none", "Don't post them", ""], ["all", "Post them with the video", ""],
        ["daily", "Post one a day", "The first at the time below after the video goes up, then one each day. Recap Studio has to be open then (or it posts it the next time it is)."]],
      c.shorts, (v) => { c.shorts = v; draw(); }),
      el("label", { class: "check yt-time" }, "Time ", time));
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
      try { await api(`${BASE}/connect`, { method: "POST" }); } catch (e) { error.textContent = e.message; return; }
      const wait = async () => {
        await loadAccount();
        drawSettings(box);
        if (account && account.connect.state === "waiting") setTimeout(wait, 2000);
      };
      wait();
    };
    const tpl = (key, label, rows) => {
      const t = el("textarea", { rows, class: "wide" });
      t.value = a.settings[key];
      t.addEventListener("change", async () => {
        account = await api(`${BASE}/settings`, { method: "PUT", json: { settings: { [key]: t.value } } });
      });
      return el("label", {}, label, t);
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
    box.replaceChildren(...[
      el("span", { class: "hint" }, "Posts a project's video and Shorts to your channel through YouTube's own API. Google lets a " +
        "program do that only through an OAuth client of your own, made once, for free:"),
      el("ol", { class: "yt-steps" },
        el("li", {}, "In Google Cloud Console (console.cloud.google.com), make a project and turn on the YouTube Data API v3."),
        el("li", {}, "Set up its sign-in (Google Auth Platform, External), then publish it: Audience → Publish app. "
          + "Left in Testing, Google signs you out every 7 days."),
        el("li", {}, "Make an OAuth client of the type Desktop app, then download its JSON or copy its ID and secret here."),
        el("li", {}, "Connect your channel: Google's sign-in page opens in your browser.")),
      el("a", { class: "yt-guide", href: GUIDE, target: "_blank", rel: "noopener" }, "The walkthrough, step by step (opens in your browser)"),
      el("span", { class: "hint warn" }, AUDIT + " YouTube also allows a Google project about 6 uploads a day: the rest go up the next day."),
      a.client ? el("span", { class: "hint" }, `OAuth client: ${a.client_id}`) : null,
      el("div", { class: "key-row" }, id, secret, el("button", { type: "button", class: "ghost small",
        onclick: () => saveClient(id.value, secret.value) }, a.client ? "Replace" : "Save")),
      el("div", { class: "key-row" }, el("label", { class: "button-like ghost small" }, "Add the client's JSON file…", file)),
      el("span", { class: "field-head" }, "Your channel"),
      el("span", { class: "hint" }, a.channel ? `Connected: ${a.channel}.` : waiting ? "Waiting for you to sign in, in your browser…"
        : a.connect.state === "error" ? `Not connected: ${a.connect.error}` : "Not connected."),
      a.blocked_until ? el("span", { class: "hint warn" }, `YouTube's daily limit is used up: posting carries on after ${a.blocked_until}.`) : null,
      el("div", { class: "key-row" },
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
          : "They need a wider permission: managing your videos, playlists and captions. Google asks for it when you connect."),
      error,
      el("span", { class: "field-head" }, "What's posted with each video"),
      tpl("description", "Description ({title}, {series}, {chapters}: its YouTube chapter list, {part})", 3),
      tpl("shorts_description", "A Short's description", 2),
      el("label", {}, "Category ", category),
      el("label", {}, "Tags (comma-separated) ", tags)].filter(Boolean));
  }

  // ---- New project: a Posting step ----

  let nc = null;  // the new project's choices
  const fresh = () => ({ when: "off", privacy: "private", shorts: "none", daily_at: "18:00", subtitles: false, playlist: "",
                         playlist_shorts: false });
  R.on("new-project-open", () => { nc = fresh(); loadAccount(); });
  R.addNewProjectPage({ id: "youtube-post", title: "Posting to YouTube", render: ({ body, state }) => {
    nc = nc || fresh();
    const s = state.settings || {};
    const draw = () => state.redraw();
    body.append(...[
      el("span", { class: "field-head" }, "When its video is made"),
      radios("yt-when", [["off", "Don't post it", "Post to YouTube… in the Video tab posts it when you say."],
        ["auto", "Post it to YouTube", "As soon as Make the recap (or Render video) finishes it."]], nc.when, (v) => { nc.when = v; draw(); })]);
    if (nc.when === "auto") {
      body.append(...[
        account && !account.channel ? el("span", { class: "hint warn" }, "No channel is connected yet: Settings → YouTube. " +
          "It posts once one is.") : null,
        account && account.channel ? el("span", { class: "hint" }, `To ${account.channel}.`) : null,
        el("span", { class: "field-head" }, "Who sees it"),
        radios("yt-privacy", PRIVACY, nc.privacy, (v) => { nc.privacy = v; draw(); }),
        el("span", { class: "hint" }, AUDIT),
        s.youtube_safe ? el("span", { class: "hint warn" }, "A YouTube-safe project is for an age-restricted upload: post it as " +
          "Private, set the age restriction in YouTube Studio, then publish it.") : null,
        shortsChoice(nc, s.orientation === "portrait", !!Number(s.shorts), draw),
        extras(nc, null, draw)].filter(Boolean));
    }
  } });
  R.on("project-created", async ({ project }) => {
    const choice = nc;
    nc = null;
    if (choice && choice.when === "auto") {
      await api(`${BASE}/projects/${project.id}`, { method: "PUT", json: { choice } });
    }
  });

  // ---- the Video tab: Post to YouTube… ----

  const dlg = el("dialog", { class: "wide-dialog yt-dialog" });
  const body = el("div", { class: "setup-body" }), actions = el("div", { class: "import-actions" });
  dlg.append(el("form", { method: "dialog", class: "import-form" },
    el("div", { class: "setup-head" }, el("h2", {}, "Post to YouTube")), body, actions));
  document.body.append(dlg);
  let st = null, c = null, again = false, lists = null;
  const STATE = { posted: "Posted", changed: "Made again since it was posted", new: "Not posted yet",
                  due: "Goes up", waiting: "After the video" };

  R.addButton("video", "Post to YouTube…", async () => {
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
    body.replaceChildren(el("div", { class: "setup-page" },
      el("span", { class: st.channel ? "hint" : "hint warn" }, st.channel ? `To ${st.channel}.` : "No channel is connected: Settings → YouTube."),
      st.blocked_until ? el("span", { class: "hint warn" }, `YouTube's daily limit is used up: posting carries on after ${st.blocked_until}.`) : null,
      st.items.length ? el("div", { class: "yt-items" }, ...st.items.map((it) => el("div", { class: `yt-item yt-${it.state}` },
        el("span", {}, el("span", { class: "tag" }, it.kind === "short" ? "Short" : "Video"), " ", it.title,
          it.subtitles ? el("span", { class: "tag" }, "subtitles") : null, it.playlist ? el("span", { class: "tag" }, "in the playlist") : null),
        el("span", { class: "yt-state" }, it.url ? el("a", { href: it.url, target: "_blank", rel: "noopener" }, `${STATE[it.state]} ${it.at || ""}`)
          : `${STATE[it.state]}${it.due ? ` ${it.due}` : ""}`))))
        : el("span", { class: "muted" }, "No video yet: Make the recap renders it."),
      el("label", {}, "Title ", title),
      el("label", { class: "wide-label" }, "Description ", desc),
      el("span", { class: "field-head" }, "Who sees it"),
      radios("yt-privacy2", PRIVACY, c.privacy, (v) => { c.privacy = v; if (v !== "private") c.publish_at = ""; draw(); }),
      el("label", { class: "check yt-time" }, "Make it public at ", at, el("span", { class: "muted" }, "(Private only; empty: stays as it is)")),
      el("span", { class: "hint" }, AUDIT),
      shortsChoice(c, st.portrait, !!Number((p.settings || {}).shorts), draw),
      extras(c, lists, draw),
      el("label", { class: "check" }, el("input", { type: "checkbox", checked: c.when === "auto",
        onchange: (e) => { c.when = e.target.checked ? "auto" : "off"; } }), " Post it by itself each time its video is made"),
      changed ? el("label", { class: "check" }, el("input", { type: "checkbox", checked: again, onchange: (e) => { again = e.target.checked; } }),
        " Also post again what was made again since it was posted") : null));
    const save = async () => { st = await api(`${BASE}/projects/${p.id}`, { method: "PUT", json: { choice: c } }); };
    actions.replaceChildren(el("button", { value: "cancel", class: "ghost" }, "Close"), el("span", { class: "grow" }),
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
