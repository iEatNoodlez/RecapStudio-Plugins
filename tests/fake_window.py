"""A stand-in for pywebview's window, for test_site_check.py (RECAP_SITECHECK_WINDOW): it opens a page the way a
browser would, only on the made-up site's stand-in check (App/tests/fakesite.py), whose page goes to /cdn-cgi/pass by
itself after a moment. It never meets a real site's check.

What it does is set by FAKE_WINDOW when the window opens: "pass" (the default: like a browser, the check's page
waits a moment, then passes), "close" (the member closes the window first) or "stay" (left open).
"""
import os
import re
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import urljoin, urlparse

import requests

AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 "
         "Safari/537.36 Edg/130.0.0.0 (stand-in window)")
windows: list = []
opened: list = []  # every window made: (title, url)


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self):
        for fn in self.handlers:
            fn()


class _Events:
    def __init__(self):
        self.closed = _Event()


class Window:
    def __init__(self, title, url):
        self.title_bar, self.url, self.events = title, url, _Events()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": AGENT, "Sec-Fetch-Mode": "navigate"})
        self.page = {"title": "", "ready": "loading", "href": url, "check": False}
        self.gone = False
        threading.Thread(target=self._browse, args=(os.environ.get("FAKE_WINDOW", "pass"),), daemon=True).start()

    def _load(self, url):
        r = self.session.get(url, timeout=10)
        title = re.search(r"<title>(.*?)</title>", r.text, re.S)
        self.page = {"title": title.group(1) if title else "", "ready": "complete", "href": r.url,
                     "check": "/cdn-cgi/challenge-platform/" in r.text}
        return r

    def _browse(self, what):
        r = self._load(self.url)
        if what == "close":
            time.sleep(1.5)
            return self.destroy()
        if what == "stay" or not self.page["check"]:
            return
        time.sleep(1.2)  # (the check's script, waiting)
        u = urlparse(r.url)
        self._load(urljoin(r.url, "/cdn-cgi/pass?to=" + requests.utils.quote(u.path + (f"?{u.query}" if u.query else ""))))

    def evaluate_js(self, script):
        p = self.page
        return [p["title"], p["ready"], p["href"], AGENT, p["check"] or p["title"].lower().startswith("just a moment")]

    def get_cookies(self):
        out = []
        for c in self.session.cookies:
            if not (urlparse(self.page["href"]).hostname or "").endswith(c.domain.lstrip(".")):
                continue
            k = SimpleCookie()
            k[c.name] = c.value
            k[c.name]["domain"] = c.domain
            k[c.name]["path"] = c.path
            k[c.name]["expires"] = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(c.expires)) if c.expires else ""
            out.append(k)
        return out

    def destroy(self):
        if not self.gone:
            self.gone = True
            if self in windows:
                windows.remove(self)
            self.events.closed.fire()


def create_window(title, url, **_):
    w = Window(title, url)
    windows.append(w)
    opened.append((title, url))
    return w
