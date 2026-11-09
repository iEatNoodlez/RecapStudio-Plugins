// Post to TikTok, a Recap Studio feature plugin: Settings → Post to TikTok (cards: your TikTok app's key and secret,
// the account, what it posts and who sees it, what goes with each post, what it has posted), a choice in the project's
// settings panel (post it when its video is made), and Post to TikTok… at Publish. The posting itself is __init__.py's.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el } = R;
  const BASE = "/api/plugins/post-to-tiktok";
  const MUSIC = "https://www.tiktok.com/legal/page/global/music-usage-confirmation/en";
  const BRANDED = "https://www.tiktok.com/legal/page/global/bc-policy/en";
  const PRIVACY = { SELF_ONLY: "Only you", MUTUAL_FOLLOW_FRIENDS: "Friends (followers you follow back)",
                    FOLLOWER_OF_CREATOR: "Your followers", PUBLIC_TO_EVERYONE: "Everyone" };
  const AUDIT = "Until TikTok audits your app, everything it posts is seen by only you, and TikTok only takes it for a " +
    "private account. That's TikTok's rule, not the app's. Once your app has passed, tick My app has passed TikTok's " +
    "audit for posting (Settings → Post to TikTok).";
  const SLOW = "TikTok can take a few minutes to process a video before it shows on your profile.";
  const link = (href, text) => el("a", { href, target: "_blank", rel: "noopener" }, text);
  const card = (title, chip, ...kids) => el("section", { class: "pk-card" }, el("h3", {}, title, chip), ...kids.filter(Boolean));
  const chip = (text, tone = "") => text ? el("span", { class: `side-chip ${tone}` }, text) : null;

  // what TikTok asks to be said before anything is posted
  const declaration = (disclosure) => el("span", { class: "hint tt-consent" }, "By posting, you agree to TikTok's ",
    ...(disclosure === "branded" || disclosure === "both" ? [link(BRANDED, "Branded Content Policy"), " and "] : []),
    link(MUSIC, "Music Usage Confirmation"), ".");

  R.addProjectSetting({ key: "post-to-tiktok.auto", label: "Post it to TikTok when its video is made:",
    options: [["no", "No"], ["yes", "Yes, as Post to TikTok… is set"]],
    hint: "By posting, you agree to TikTok's Music Usage Confirmation. What it posts and who sees it: Post to TikTok… at Publish." });

  let account = null;
  async function loadAccount() {
    try { account = await api(`${BASE}/account`); } catch { account = null; }
    return account;
  }
  const saveSettings = async (change) => { account = await api(`${BASE}/settings`, { method: "PUT", json: { settings: change } }); };

  // who sees it: no default (TikTok's rule), only what TikTok offers this account; Only you until the app is audited
  // (nothing to pick then: what's drawn instead says so)
  function privacyPick(value, offered, audited, onpick, emptyText = "Choose…") {
    if (!audited) return null;
    const options = (offered && offered.length ? offered : Object.keys(PRIVACY));
    const pick = el("select", { onchange: (e) => onpick(e.target.value) }, el("option", { value: "" }, emptyText),
      ...options.map((v) => el("option", { value: v }, PRIVACY[v] || v)));
    pick.value = options.includes(value) ? value : "";
    return el("label", { class: "pk-line" }, "Who sees it", pick);
  }

  // comments, duets and stitches: off until ticked (TikTok's rule), and greyed out when the account doesn't allow them
  function interactions(s, creator, onchange) {
    const off = { comments: creator && creator.comments_off, duet: creator && creator.duet_off, stitch: creator && creator.stitch_off };
    return el("div", { class: "pk-ticks" }, ...[["comments", "Allow comments"], ["duet", "Allow duets"], ["stitch", "Allow stitches"]]
      .map(([k, label]) => el("label", { class: `check${off[k] ? " muted" : ""}`, title: off[k] ? "This account doesn't allow it (its TikTok settings)" : "" },
        el("input", { type: "checkbox", checked: !!s[k] && !off[k], disabled: !!off[k], onchange: (e) => onchange({ [k]: e.target.checked }) }),
        ` ${label}`)));
  }

  // ---- Settings → Post to TikTok ----

  R.addSettings("Post to TikTok", async (box) => {
    await loadAccount();
    drawSettings(box);
  });

  function drawSettings(box) {
    if (!account) { box.replaceChildren(el("span", { class: "error" }, "Couldn't reach the plugin.")); return; }
    const a = account, s = a.settings, error = el("div", { class: "error" });
    const redraw = () => drawSettings(box);
    const change = async (c) => { try { await saveSettings(c); } catch (e) { error.textContent = e.message; } redraw(); };
    const key = el("input", { placeholder: "Client key", spellcheck: "false", autocomplete: "off" });
    const secret = el("input", { type: "password", placeholder: "Client secret", autocomplete: "new-password" });
    const connect = async () => {
      let r;
      try { r = await api(`${BASE}/connect`, { method: "POST" }); } catch (e) { error.textContent = e.message; return; }
      if (r.server && r.url) window.open(r.url, "_blank", "noopener");  // (a server: in this browser, not the server's)
      const wait = async () => {
        await loadAccount();
        redraw();
        if (account && account.connect.state === "waiting") setTimeout(wait, 2000);
      };
      wait();
    };
    // a server: TikTok sends the browser to an address on this computer (127.0.0.1) that it can't open; pasted here,
    // it finishes the sign-in on the server
    const pasteBox = () => {
      const address = el("input", { placeholder: "http://127.0.0.1:…/callback/?code=…&state=…", spellcheck: "false" });
      return el("div", { class: "pk-card" }, el("span", { class: "hint" }, "Recap Studio runs on a server here, so once you allow it, your " +
        "browser goes to an address starting with http://127.0.0.1 that doesn't open. Copy that whole address from the " +
        "address bar and paste it here:"), el("div", { class: "pk-row" }, address, el("button", { type: "button", class: "ghost small",
        onclick: async () => {
          try { await api(`${BASE}/connect/paste`, { method: "POST", json: { address: address.value } }); }
          catch (e) { error.textContent = e.message; }
          await loadAccount();
          redraw();
        } }, "Finish connecting")));
    };
    const user = a.user ? (a.user.nickname || a.user.name || "your account") + (a.user.username ? ` (@${a.user.username})` : "") : "";
    const waiting = a.connect.state === "waiting";
    const hashtags = el("input", { value: s.hashtags, placeholder: "#manga #mangarecap", spellcheck: "false",
      onchange: (e) => change({ hashtags: e.target.value }) });
    const disclosure = el("select", { onchange: (e) => change({ disclosure: e.target.value }) },
      el("option", { value: "none" }, "None (it promotes nothing)"),
      el("option", { value: "yours" }, "Your brand (labelled \"Promotional content\")"),
      el("option", { value: "branded" }, "Branded content, for someone else (labelled \"Paid partnership\")"),
      el("option", { value: "both" }, "Both"));
    disclosure.value = s.disclosure;
    const set = el("select", { onchange: (e) => change({ set: e.target.value }) },
      el("option", { value: "video" }, "The whole video (or its parts, when the app splits it)"),
      el("option", { value: "shorts" }, "Its Shorts"),
      el("option", { value: "pieces" }, "Advanced Cutting's pieces"));
    set.value = s.set;
    const tone = a.user ? "ok" : waiting ? "on" : a.connect.state === "error" ? "bad" : "";
    box.replaceChildren(...[
      el("span", { class: "hint" }, "Posts a project's video to your TikTok account through TikTok's own API, from Publish " +
        "(Post to TikTok…) or each time a project's video is made, when its own settings say so."),
      card("Your TikTok app", chip(a.client ? "set" : "needed", a.client ? "ok" : "warn"),
        el("span", { class: "hint" }, "TikTok lets a program post only through a TikTok app of your own, made once, for free:"),
        el("ol", { class: "pk-steps" },
          el("li", {}, "On TikTok for Developers (developers.tiktok.com), Manage apps → Connect an app. Pick Desktop as its platform."),
          el("li", {}, "Add Login Kit to it, with this redirect URI: ", el("code", {}, a.redirect)),
          el("li", {}, "Add the Content Posting API, and switch on Direct Post."),
          el("li", {}, "Copy the app's client key and client secret here. Try it in a Sandbox (with your account as a target user), or submit the app for review."),
          el("li", {}, "Connect your account, below: TikTok's sign-in page opens in your browser.")),
        a.client ? el("span", { class: "hint" }, `Your app's client key: ${a.client_key} (kept on this PC only, with its secret).`) : null,
        el("div", { class: "pk-row" }, key, secret, el("button", { type: "button", class: "ghost small", onclick: async () => {
          try { account = await api(`${BASE}/client`, { method: "PUT", json: { client_key: key.value, client_secret: secret.value } }); redraw(); }
          catch (e) { error.textContent = e.message; }
        } }, a.client ? "Replace" : "Save"))),
      card("Your account", chip(a.user ? "connected" : waiting ? "waiting" : "", tone),
        el("div", { class: "pk-status" }, el("span", { class: `pk-dot ${tone}` }),
          el("span", {}, a.user ? `Connected: ${user}.` : waiting ? "Waiting for you to sign in, in your browser…"
            : a.connect.state === "error" ? `Not connected: ${a.connect.error}` : "Not connected.")),
        waiting && a.server ? pasteBox() : null,
        a.blocked ? el("span", { class: "hint warn" }, `${a.blocked.why}: posting carries on after ${a.blocked.until}.`) : null,
        el("div", { class: "pk-actions" },
          el("button", { type: "button", class: "ghost small", disabled: !a.client || waiting, onclick: connect },
            a.user ? "Connect another account" : "Connect your account"),
          a.user ? el("button", { type: "button", class: "ghost small danger", onclick: async () => {
            if (!await R.ask("Disconnect the account?", "Recap Studio's access to it is taken back; nothing on TikTok changes.", { ok: "Disconnect" })) return;
            account = await api(`${BASE}/disconnect`, { method: "POST" });
            redraw();
          } }, "Disconnect") : null)),
      error,
      card("Posting", null,
        el("label", { class: "check" }, el("input", { type: "checkbox", checked: !!s.audited,
          onchange: (e) => change({ audited: e.target.checked }) }), " My app has passed TikTok's audit for posting"),
        el("span", { class: s.audited ? "hint" : "hint warn" }, s.audited ? "Then who sees each post is yours to choose. TikTok still decides when a post is public: " +
          "the app only says it's public once TikTok does." : AUDIT),
        privacyPick(s.privacy, null, s.audited, (v) => change({ privacy: v }), "Choose for each post"),
        el("label", { class: "pk-line" }, "What it posts, unless a project says otherwise", set),
        el("span", { class: "hint" }, "TikTok takes videos of up to 10 minutes from most accounts (some only 3 or 5), " +
          "and about 15 posts a day. Several go up as a series: \"Part 1 of 5\" and so on, joined by one hashtag. " +
          "A whole recap is usually longer: its Shorts, or Advanced Cutting's pieces cut by length, fit.")),
      card("With each post", null,
        interactions(s, null, (c) => change(c)),
        el("label", { class: "check" }, el("input", { type: "checkbox", checked: !!s.aigc,
          onchange: (e) => change({ aigc: e.target.checked }) }), " Label it as AI-generated (TikTok's label, in its description)"),
        el("div", { class: "pk-fields" }, el("label", { class: "pk-field" }, "Content disclosure", disclosure),
          el("label", { class: "pk-field" }, "Hashtags on every post", hashtags)),
        declaration(s.disclosure)),
      card("What it has posted", null,
        a.log.length ? el("div", { class: "pk-log" }, ...a.log.map((x) => el("div", {},
          el("span", { class: "pk-at" }, x.at), el("span", {}, el("b", {}, `${x.project}: `), x.note))))
          : el("span", { class: "pk-empty" }, "Nothing yet."))].filter(Boolean));
  }

  // ---- Publish: Post to TikTok… ----

  const dlg = el("dialog", { class: "wide-dialog tt-dialog" });
  const body = el("div", { class: "setup-body" }), actions = el("div", { class: "import-actions" });
  dlg.append(el("form", { method: "dialog", class: "import-form", novalidate: true },
    el("div", { class: "setup-head" }, el("h2", {}, "Post to TikTok")), body, actions));
  document.body.append(dlg);
  let st = null, c = null, auto = "no", again = false, creator = null, creatorError = "";

  // at Publish (apps from 0.4.3), else with the video's buttons
  R.addButton(R.places && R.places.includes("publish") ? "publish" : "video", "Post to TikTok…", async () => {
    const p = R.current();
    if (!p) return;
    try { st = await api(`${BASE}/projects/${p.id}`); } catch (e) { return R.notify(e.message); }
    c = { ...st.choice };
    auto = st.auto;
    again = false;
    creator = null;
    creatorError = "";
    drawPost();
    dlg.showModal();
    if (st.user) {  // (asked fresh each time, as TikTok wants: the account's name, who may see it, what it allows)
      try { creator = await api(`${BASE}/creator`); } catch (e) { creatorError = e.message; }
      if (dlg.open) drawPost();
    }
  }, { title: "Post this project's video (or its parts, Shorts or pieces) to your TikTok account" });

  function stateText(it) {
    if (it.state === "sending") return "Sent; TikTok is processing it";
    if (it.state === "changed") return "Made again since it was posted";
    if (it.state !== "posted") return "Not posted yet";
    const seen = it.privacy === "PUBLIC_TO_EVERYONE" ? (it.public ? "public" : "not public yet")
      : it.privacy === "SELF_ONLY" ? "only you" : (PRIVACY[it.privacy] || "").toLowerCase();
    return `Posted ${it.at || ""}${seen ? `, ${seen}` : ""}`;
  }
  const stateTone = (it) => it.state === "posted" ? "ok" : it.state === "changed" || it.state === "sending" ? "warn" : "";

  function drawPost() {
    const p = R.current(), s = st.settings;
    const save = async () => { st = await api(`${BASE}/projects/${p.id}`, { method: "PUT", json: { choice: c, auto } }); };
    const pickSet = async (v) => {
      c.set = v;
      try { await save(); } catch (e) { R.notify(e.message); }
      drawPost();
    };
    const changeSettings = async (change) => {
      try { await saveSettings(change); st.settings = account.settings; } catch (e) { R.notify(e.message); }
      drawPost();
    };
    const n = st.sets, changed = st.items.some((it) => it.state === "changed");
    const title = el("input", { value: c.title, placeholder: st.title, oninput: (e) => { c.title = e.target.value; } });
    const desc = el("textarea", { rows: 3, placeholder: "A description under the title (optional)", oninput: (e) => { c.description = e.target.value; } });
    desc.value = c.description;
    const tag = el("input", { value: c.tag, placeholder: st.default_tag, spellcheck: "false", oninput: (e) => { c.tag = e.target.value; } });
    const who = creator ? `${creator.nickname || "your account"}${creator.username ? ` (@${creator.username})` : ""}`
      : st.user ? (st.user.nickname || st.user.name || "your account") : "";
    const privacy = c.privacy || s.privacy;
    const ready = st.user && st.items.length && (!s.audited || privacy) && !creatorError;
    // what it posts: the whole video (or its parts), its Shorts, or Advanced Cutting's pieces, as pills
    const SETS = [["video", n.video > 1 ? `Its ${n.video} parts` : "The whole video", n.video ? "" : "Not made yet."],
      ["shorts", `Its Shorts (${n.shorts})`, n.shorts ? "" : "None yet: the project cuts them when How its video looks says so."],
      ["pieces", `Advanced Cutting's pieces (${n.pieces})`, n.pieces ? "" : "None yet: Cut the video… (by length, for TikTok)."]];
    const pills = el("div", { class: "pk-pills" }, ...SETS.map(([id, text]) => el("label", { class: `pk-pill${st.set === id ? " on" : ""}` },
      el("input", { type: "radio", name: "tt-set2", checked: st.set === id, onchange: () => pickSet(id) }), text)));
    const chosen = SETS.find(([id]) => id === st.set);
    body.replaceChildren(el("div", { class: "setup-page" }, ...[
      el("span", { class: st.user ? "hint" : "hint warn" }, st.user ? `To ${who}.` : "No TikTok account is connected: Settings → Post to TikTok."),
      creatorError ? el("span", { class: "hint warn" }, `TikTok can't take a post from this account now: ${creatorError}. Try again later.`) : null,
      creator && !s.audited && creator.public_account ? el("span", { class: "hint warn" }, "Your TikTok account is public. Until your app " +
        "passes TikTok's audit, TikTok only takes its posts for a private account: in TikTok, Settings and privacy → Privacy → Private account.") : null,
      st.blocked ? el("span", { class: "hint warn" }, `${st.blocked.why}: posting carries on after ${st.blocked.until}.`) : null,
      el("div", { class: "pk-cols" },
        el("div", { class: "pk-col" },
          card("What it posts", null, pills,
            chosen && chosen[2] && !st.items.length ? el("span", { class: "hint warn" }, chosen[2]) : null,
            st.items.length ? el("div", { class: "pk-list" }, ...st.items.map((it) => el("div", { class: "pk-item" },
              el("span", { class: "pk-main", title: it.caption }, el("span", {}, it.title), el("span", { class: "muted tt-file" }, it.file)),
              el("span", { class: `pk-state ${stateTone(it)}` }, it.url ? link(it.url, stateText(it)) : stateText(it)))))
              : chosen && chosen[2] ? null : el("span", { class: "pk-empty" }, st.why),
            st.preview ? el("video", { class: "tt-preview", src: `/files/${p.id}/${st.preview.split("/").map(encodeURIComponent).join("/")}`,
              controls: true, preload: "metadata" }) : null),
          card("The caption", null,
            el("label", { class: "pk-field" }, "Title", title),
            el("label", { class: "pk-field" }, "Description", desc),
            el("label", { class: "pk-field" }, "The series' hashtag", tag),
            el("span", { class: "hint" }, st.items.length > 1 ? `Each caption says "Part 1 of ${st.items.length}" and so on, and ends with ` +
              `"Every part: #${st.tag}". TikTok's API can't add comments, playlists or links, so the hashtag is what joins them.` : `It ends with #${st.tag}.`))),
        el("div", { class: "pk-col" },
          card("Who sees it", null,
            privacyPick(c.privacy || s.privacy, creator && creator.privacy, s.audited, (v) => { c.privacy = v; drawPost(); }),
            s.audited ? null : el("span", { class: "hint warn" }, AUDIT),
            interactions(s, creator, changeSettings),
            el("span", { class: "hint" }, SLOW)),
          card("When", null,
            el("label", { class: "check" }, el("input", { type: "checkbox", checked: auto === "yes",
              onchange: (e) => { auto = e.target.checked ? "yes" : "no"; } }), " Post it by itself each time its video is made"),
            changed ? el("label", { class: "check" }, el("input", { type: "checkbox", checked: again, onchange: (e) => { again = e.target.checked; } }),
              " Also post what was made again since it was posted") : null),
          declaration(s.disclosure)))].filter(Boolean)));
    actions.replaceChildren(el("button", { value: "cancel", class: "ghost", formnovalidate: true }, "Close"), el("span", { class: "grow" }),
      el("button", { type: "button", class: "ghost", onclick: async () => {
        try { await save(); dlg.close(); } catch (e) { R.notify(e.message); }
      } }, "Save"),
      el("button", { type: "button", class: "primary", disabled: !ready, title: s.audited && !privacy ? "Choose who sees it first" : "",
        onclick: async () => {
          try {
            await save();
            await api(`${BASE}/projects/${p.id}/post`, { method: "POST", json: { again } });
          } catch (e) { return R.notify(e.message, "Not posted"); }
          dlg.close();
          R.reload();
        } }, "Post now"));
  }
})();
