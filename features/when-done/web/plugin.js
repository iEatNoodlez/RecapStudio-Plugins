// When a video is done, a Recap Studio feature plugin: its section in Settings (a card each: copy to a folder, run a
// command, tell your phone; then what it did), a choice in Project settings to leave a project out, and "Do it now"
// with the finished video's buttons. The doing is __init__.py's.
(() => {
  const R = window.RecapStudio;
  if (!R || R.version < 1) return;
  const { api, el } = R;
  const BASE = "/api/plugins/when-done";

  R.addProjectSetting({ key: "when-done.use", label: "When its video is done:",
    options: [["yes", "Copy, run and tell, as set in Settings"], ["no", "Nothing (leave this project out)"]] });

  R.addButton("video", "Copy, run and tell now", async () => {
    try {
      await api(`${BASE}/now/${R.current().id}`, { method: "POST" });
      R.notify("What's set in Settings → When a video is done is being done now. Settings lists what happened.", "Started");
    } catch (e) { R.notify(e.message); }
  }, { title: "Do what Settings → When a video is done says, for this video, now" });

  R.addSettings("When a video is done", async (box) => {
    let st;
    try { st = await api(`${BASE}/status`); } catch (e) { box.replaceChildren(el("span", { class: "error" }, e.message)); return; }
    draw(box, st);
  });

  // a topic nobody would guess: anyone who knows a topic's name can read what's posted to it
  const randomTopic = () => "recap-studio-" + Array.from(crypto.getRandomValues(new Uint8Array(8)),
    (b) => "abcdefghijklmnopqrstuvwxyz0123456789"[b % 36]).join("");

  function draw(box, st) {
    const s = st.settings;
    const error = el("div", { class: "error" });
    const save = async (change) => {
      try { draw(box, await api(`${BASE}/settings`, { method: "PUT", json: { changes: change } })); }
      catch (e) { error.textContent = e.message; }
    };
    const text = (key, placeholder, extra = {}) => el("input", { value: s[key], placeholder, spellcheck: "false",
      ...extra, onchange: (e) => save({ [key]: e.target.value }) });
    const tick = (key, label) => el("label", { class: "check" },
      el("input", { type: "checkbox", checked: !!s[key], onchange: (e) => save({ [key]: e.target.checked }) }), ` ${label}`);
    const chip = (on) => el("span", { class: `side-chip${on ? " ok" : ""}` }, on ? "on" : "off");
    const linux = st.windows === false;
    const copy = el("section", { class: "pk-card" }, el("h3", {}, "Copy it to a folder", chip(s.folder)),
      el("div", { class: "pk-row" }, text("folder", `Off; or a folder, e.g. ${linux ? "/mnt/nas/Finished recaps" : "D:\\Finished recaps"}`),
        el("button", { type: "button", class: "ghost small", onclick: async () => {
          const picked = await R.pickFolder(s.folder);
          if (picked) save({ folder: picked });
        } }, "Choose…")),
      el("div", { class: "pk-ticks" }, tick("own_folder", "In a folder named after the project"),
        tick("srt", "Its subtitles (.srt)"), tick("thumbnail", "Its thumbnail"), tick("shorts", "Its Shorts")));
    const command = el("section", { class: "pk-card" }, el("h3", {}, "Run a command", chip(s.command)),
      text("command", linux ? "Off; or a command, e.g. rsync -a {copy} nas:recaps/"
        : "Off; or a command, e.g. robocopy {folder} \\\\nas\\recaps *.mp4", { class: "pk-mono" }),
      el("span", { class: "hint" }, "Run in the project's output folder. Filled in, each in quotes: " +
        st.places.map((p) => `{${p}}`).join(" ") + " ({copy}: the copied video; {videos}: every part). They're also " +
        "in the variables RECAP_VIDEO, RECAP_PROJECT and so on."));
    const phone = el("section", { class: "pk-card" }, el("h3", {}, "Tell your phone", chip(s.ntfy_topic || s.webhook)),
      el("span", { class: "hint" }, "With ntfy (a free app for Android and iPhone): install it, subscribe to the topic " +
        "below, and this PC posts to it. Anyone who knows a topic's name can read it, so keep the one made up here. " +
        "Or a Discord or Slack webhook (any other gets the details as JSON)."),
      el("div", { class: "pk-grid fill" },
        el("span", {}, "ntfy topic"), el("div", { class: "pk-row" }, text("ntfy_topic", "Off"),
          s.ntfy_topic ? null : el("button", { type: "button", class: "ghost small", onclick: () => save({ ntfy_topic: randomTopic() }) }, "Make one up")),
        el("span", {}, "ntfy server"), text("ntfy_server", "https://ntfy.sh"),
        el("span", {}, "Webhook"), text("webhook", "Off; or https://discord.com/api/webhooks/…")),
      el("div", { class: "pk-ticks" }, tick("tell_done", "When a video is done"),
        tick("tell_asks", "When a recap stops to ask you something"), tick("tell_failed", "When a recap fails")),
      s.ntfy_topic || s.webhook ? el("div", { class: "pk-actions" }, el("button", { type: "button", class: "ghost small",
        onclick: async () => {
          try { await api(`${BASE}/test`, { method: "POST" }); error.textContent = ""; R.notify("Sent: check your phone.", "Test sent"); }
          catch (e) { error.textContent = e.message; }
        } }, "Send a test")) : null);
    const log = st.log.length ? el("section", { class: "pk-card" }, el("h3", {}, "What it did"),
      el("div", { class: "pk-log" }, ...st.log.slice(0, 8).map((x) => el("div", {}, el("span", { class: "pk-at" }, x.at),
        el("span", {}, el("b", {}, `${x.project}: `), x.note))))) : null;
    box.replaceChildren(...[
      el("span", { class: "hint" }, "Done each time a project's video is made (Make the recap or Render video), " +
        "while Recap Studio is open. A project can be left out in its own settings, beside its stages."),
      copy, command, phone, error, log].filter(Boolean));
  }
})();
