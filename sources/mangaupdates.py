"""MangaUpdates: series details for Recap Studio, from MangaUpdates' public API (no account or key needed).

A source plugin that gives no chapters (CHAPTERS = False) and no characters (MangaUpdates doesn't list them): search()
finds a series by any of its titles, and series() gives its details. Find the series uses them to name a new project
and set its kind. MangaUpdates lists nearly every series that has been scanlated, so it finds many that AniList
doesn't have. Novels and drama CDs are left out.

MangaUpdates' API: https://api.mangaupdates.com (its acceptable use policy asks for credit, which Find the series
gives by showing each result's source, and for spacing and caching requests, which this does).
"""
import html
import re
import time

import requests

RECAP_PLUGIN = 1
NAME = "MangaUpdates"
VERSION = "1.0.0"
SITES = ["mangaupdates.com"]
LANGUAGE = "en"
NSFW = False
CHAPTERS = False                 # series' details only (no chapters, no characters)

API = "https://api.mangaupdates.com/v1"
PER_PAGE = 20
SPACING = 1.0                    # seconds between requests, at least
KEEP = 3600                      # seconds an answer is kept (the policy asks for caching)
NOT_COMICS = {"Novel", "Drama CD"}
_session = requests.Session()
_session.headers.update({"Accept": "application/json", "User-Agent": "RecapStudio-MangaUpdates/1.0"})
_last = [0.0]
_kept: dict = {}                 # (method, path, body): (time, answer)


def _ask(method: str, path: str, body: dict | None = None) -> dict:
    """One request, spaced from the last and kept for an hour."""
    key = (method, path, repr(sorted((body or {}).items())))
    if key in _kept and time.time() - _kept[key][0] < KEEP:
        return _kept[key][1]
    time.sleep(max(0.0, _last[0] + SPACING - time.time()))
    _last[0] = time.time()
    r = _session.request(method, API + path, json=body, timeout=30)
    if r.status_code == 404:
        raise ValueError("MangaUpdates has no such series")
    if r.status_code in (429, 503):
        raise RuntimeError("MangaUpdates is busy: try again in a minute")
    r.raise_for_status()
    try:
        found = r.json()
    except ValueError:
        raise RuntimeError("MangaUpdates gave an answer that isn't JSON")
    _kept[key] = (time.time(), found)
    return found


def _clean(text, most: int) -> str:
    """A MangaUpdates description (plain text with some HTML, Markdown links and BBCode) as plain text."""
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)                          # [words](link)
    text = re.sub(r"\[url=[^\]]*\]([^\[]*)\[/url\]", r"\1", text, flags=re.I)     # [url=link]words[/url]
    text = re.sub(r"\[/?(b|i|u|s|url|spoiler)\]", "", text, flags=re.I)
    text = re.sub(r"\(Source: [^)]*\)", "", text, flags=re.I)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text).strip()
    if len(text) > most:
        cut = text[:most]
        end = max(cut.rfind(". "), cut.rfind(".\n"))
        text = cut[:end + 1] if end > most // 3 else cut.rsplit(" ", 1)[0] + "…"
    return text.strip()


def _kind(kind: str) -> str:
    """MangaUpdates' type as the app's kind of comic: manga, webtoon (Korea's and China's), or comic."""
    return "manga" if kind in ("Manga", "Doujinshi") else "webtoon" if kind in ("Manhwa", "Manhua") else "comic"


def _card(m: dict, most: int = 600) -> dict:
    year = str(m.get("year") or "")
    return {"url": m.get("url") or "", "title": html.unescape(m.get("title") or "") or "Untitled",
            "cover": ((m.get("image") or {}).get("url") or {}).get("original"),
            "summary": _clean(m.get("description"), most), "kind": _kind(m.get("type") or ""),
            "year": int(year[:4]) if year[:4].isdigit() else None}


def _id(url: str) -> int:
    """The series' number in the API: its link's short id is that number in base 36."""
    m = re.search(r"mangaupdates\.com/series/([0-9a-z]+)", url.strip(), re.I)
    if m:
        return int(m.group(1), 36)
    if re.fullmatch(r"\d+", url.strip()):
        return int(url.strip())
    raise ValueError("That isn't a MangaUpdates series link (https://www.mangaupdates.com/series/...)")


def search(query: str, page: int = 1) -> dict:
    """Series whose titles (the other names too) match, the closest first."""
    found = _ask("POST", "/series/search", {"search": query, "page": page, "perpage": PER_PAGE})
    items = [_card(x["record"], 300) for x in found.get("results") or []
             if (x.get("record") or {}).get("type") not in NOT_COMICS and (x.get("record") or {}).get("url")]
    return {"items": items, "more": page * PER_PAGE < int(found.get("total_hits") or 0)}


def series(url: str) -> dict:
    """A series' details (no chapters: MangaUpdates only lists who released them)."""
    m = _ask("GET", f"/series/{_id(url)}")
    card = _card(m, 1500)
    names = [html.unescape(a["title"]) for a in m.get("associated") or [] if a.get("title")]
    if names:  # its other names (the English one, the original), for the narrator and the reader
        card["summary"] = (card["summary"] + f"\n\nAlso known as: {', '.join(names[:8])}").strip()
    return {**card, "chapters": []}
