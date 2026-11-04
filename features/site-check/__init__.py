"""Site check, a Recap Studio feature plugin: passing a site's check for a person (Cloudflare's) yourself, in a window,
so your own sources can get chapters from a site that has one.

Some sites answer programs with Cloudflare's check (a page that runs a script in a browser, sometimes with a box to
tick) instead of their pages. When one does, the app says so (sources.SiteCheck, and the "site_check" event), and the
page script offers to open that site in a small window of the app's own browser (Edge WebView2, like the app's
window). You pass the check there: wait while it runs, or tick its box. The window is only ever opened when you ask,
and nothing here clicks or answers anything in it.

Once the site's own page shows, the window closes, and the site's cookies (with Cloudflare's pass, cf_clearance) and
the window's browser name, which the pass is tied to, go to the app (plugin.site_pass): your source plugins' requests
to that site and the app's picture downloads carry them until the pass runs out (the site decides how long: often 30
minutes, sometimes days). Then the site asks again, and so does the app.

Passes are kept in passes.json in its data folder (they're for this PC and this connection), so one outlives a
restart. A site that refuses the app even with a fresh pass looks at more than the pass (how the connection itself
looks, which only a real browser has): a window can't help with that one, and the page says so.

Only in the app's own window: when Recap Studio is opened in a browser (--browser), there's no window to open.

For tests only: RECAP_SITECHECK_WINDOW, a Python file standing in for pywebview (its create_window() and windows).
"""
import importlib.util
import json
import os
import sys
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from fastapi import Body, HTTPException

from recap import sources

WINDOW_TIMEOUT = 600   # seconds the window waits for the check to be passed
SETTLE = 3             # seconds the site's own page has to show, when it gives no cf_clearance, before it's taken
DEFAULT_LIFE = 30 * 60  # a pass's life when its cookie doesn't say (Cloudflare's usual)
FRESH = 5 * 60         # a pass this new that's refused: the site looks at more than the pass
# What the window's page is asked: whether it's Cloudflare's check (its script, form or widget, or its title), and
# where it is.
LOOK = ("[document.title, document.readyState, location.href, navigator.userAgent, !!document.querySelector("
        "'script[src*=\"/cdn-cgi/challenge-platform/\"], #challenge-form, #challenge-running, #cf-challenge-running, "
        "[id^=\"cf-chl\"], .cf-turnstile') || /^just a moment/i.test(document.title)]")

_plugin = None
_lock = threading.Lock()
_passes: dict = {}   # domain: {"cookies", "user_agent", "expires", "got", "host", "clearance"}
_waiting: dict = {}  # host: {"url", "source", "refused", "stubborn", "at"}: sites that asked, not passed since
_window = {"state": "idle", "host": "", "url": "", "message": "", "at": 0.0}


def _gui():
    """pywebview, when the app is in its own window (or the stand-in tests give); else None."""
    path = os.environ.get("RECAP_SITECHECK_WINDOW")
    if path:
        name = "recap_sitecheck_window"
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        return sys.modules[name]
    gui = sys.modules.get("webview")
    return gui if gui is not None and getattr(gui, "windows", None) else None


WHY_NOT = ("Site check needs Recap Studio's own window, and it's open in a browser now: start it the usual way "
           "(its shortcut) to pass a site's check")


def _save() -> None:
    _plugin.data_dir.mkdir(parents=True, exist_ok=True)
    (_plugin.data_dir / "passes.json").write_text(json.dumps(_passes, indent=1), encoding="utf-8")


def _restore() -> None:
    """The passes kept from before that haven't run out, given to the app again."""
    try:
        kept = json.loads((_plugin.data_dir / "passes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    now = time.time()
    with _lock:
        _passes.clear()
        for domain, p in kept.items() if isinstance(kept, dict) else []:
            try:
                if p["expires"] > now:
                    _plugin.site_pass(domain, p["cookies"], p["user_agent"], p["expires"])
                    _passes[domain] = p
            except (KeyError, TypeError, sources.SourceError):
                continue


def _expiry(value: str) -> float | None:
    try:
        when = parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, IndexError):
        return None
    return when if when > time.time() + 60 else None  # (a session cookie's comes as long ago)


def _cookies(found, host: str) -> dict:
    """The window's cookies for the site: {name: {"value", "domain", "expires"}}."""
    out = {}
    for c in found or []:
        for name, m in c.items():
            domain = (m["domain"] or host).lower().lstrip(".")
            if sources.on_site(host, domain) or sources.on_site(domain, host):
                out[name] = {"value": m.value, "domain": domain, "expires": _expiry(m["expires"])}
    return out


def on_check(host, url, source="", refused=False, **_):
    """A site asked for its check (sources.site_check): listed, to pass from Settings or when the page offers."""
    with _lock:
        had = next((p for d, p in _passes.items() if sources.on_site(host, d)), None)
        stubborn = bool(refused and had and time.time() - had.get("got", 0) < FRESH)
        _waiting[host] = {"url": url, "source": source, "refused": bool(refused), "stubborn": stubborn,
                          "at": time.time()}


def _take(host: str, cookies: dict, user_agent: str) -> str:
    """The window's pass, given to the app and kept: the domain it's for."""
    clearance = cookies.get("cf_clearance")
    domain = clearance["domain"] if clearance else host
    expires = (clearance or {}).get("expires") or time.time() + DEFAULT_LIFE
    values = {k: v["value"] for k, v in cookies.items()}
    _plugin.site_pass(domain, values, user_agent, expires)
    with _lock:
        _passes[domain] = {"cookies": values, "user_agent": user_agent, "expires": expires, "got": time.time(),
                           "host": host, "clearance": bool(clearance)}
        for h in [h for h in _waiting if sources.on_site(h, domain)]:
            _waiting.pop(h)
        _save()
    return domain


def _run(gui, url: str, host: str) -> None:
    """The window, until the site's own page shows (the check passed), it's closed, or it's been open too long."""
    def end(state, message):
        _window.update(state=state, message=message, at=time.time())

    try:
        w = gui.create_window(f"Pass {host}'s check (Recap Studio)", url, width=520, height=720)
    except Exception as e:
        return end("failed", f"The window didn't open: {e}")
    closed = threading.Event()
    w.events.closed += lambda *a: closed.set()
    start, calm = time.time(), 0
    try:
        while time.time() - start < WINDOW_TIMEOUT:
            if closed.wait(1):
                return end("closed", f"The window was closed before {host}'s check was passed")
            try:
                title, ready, here, agent, checking = w.evaluate_js(LOOK)
                cookies = _cookies(w.get_cookies(), host)
            except Exception:  # (between pages, or still loading)
                continue
            on_it = sources.on_site((urlparse(str(here)).hostname or "").lower(), host.removeprefix("www."))
            if checking or ready != "complete" or not on_it:
                calm = 0
                continue
            calm += 1
            if "cf_clearance" in cookies or calm >= SETTLE:
                domain = _take(host, cookies, str(agent))
                return end("passed", f"{host}: passed. Recap Studio uses that pass for {domain} until it runs out")
        end("timeout", f"{host}'s check wasn't passed in {WINDOW_TIMEOUT // 60} minutes, so the window was closed")
    finally:
        if not closed.is_set():
            try:
                w.destroy()
            except Exception:
                pass


def status() -> dict:
    now = time.time()
    gui = _gui()
    with _lock:
        passes = [{"domain": d, "host": p.get("host", d), "expires": p["expires"], "got": p.get("got"),
                   "clearance": p.get("clearance", True)} for d, p in sorted(_passes.items()) if p["expires"] > now]
        waiting = [{"host": h, **w} for h, w in sorted(_waiting.items(), key=lambda x: -x[1]["at"])]
    return {"can_open": gui is not None, "why": "" if gui else WHY_NOT, "window": dict(_window),
            "passes": passes, "waiting": waiting}


def setup(plugin):
    global _plugin
    if not hasattr(plugin, "site_pass"):
        raise RuntimeError("It needs Recap Studio 0.2.4 or later (Settings → Updates → Check now)")
    _plugin = plugin
    _restore()
    plugin.on("site_check", on_check)

    @plugin.router.get("/status")
    def get_status():
        return status()

    @plugin.router.post("/open")
    def open_site(url: str = Body(..., embed=True)):
        """Open a site in a window for the member to pass its check (only when they ask: the page's button)."""
        url = url.strip()
        u = urlparse(url if "://" in url else "https://" + url)
        host = (u.hostname or "").lower()
        if u.scheme not in ("http", "https") or not host:
            raise HTTPException(400, "That isn't a site's address")
        gui = _gui()
        if gui is None:
            raise HTTPException(409, WHY_NOT)
        with _lock:
            if _window["state"] == "open":
                raise HTTPException(409, f"{_window['host']}'s window is open already: pass its check or close it first")
            _window.update(state="open", host=host, url=u.geturl(), message=f"Waiting for {host}'s check to be passed",
                           at=time.time())
        threading.Thread(target=_run, args=(gui, u.geturl(), host), daemon=True, name="site-check").start()
        return status()

    @plugin.router.delete("/passes/{domain}")
    def forget(domain: str):
        plugin.forget_site_pass(domain)
        with _lock:
            _passes.pop(domain, None)
            _save()
        return status()

    @plugin.router.delete("/waiting/{host}")
    def dismiss(host: str):
        with _lock:
            _waiting.pop(host, None)
        return status()
