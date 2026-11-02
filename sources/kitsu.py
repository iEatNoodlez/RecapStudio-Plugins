"""Kitsu: series details and characters for Recap Studio, from Kitsu's public API (no account or key needed).

A source plugin that gives no chapters (CHAPTERS = False): search() finds a series by its titles, series() gives its
details, and characters() its characters, main ones first, each with its other names (its Japanese name too), role,
a short description and a picture. Much of Kitsu's character data came from MyAnimeList, so it stands in for it
(Jikan, the public API that read MyAnimeList, shut down in October 2026). Its characters are thorough for older and
well-known Japanese manga and missing for most newer series and webtoons; it has no genders. Novels are left out.

Kitsu's API: https://kitsu.docs.apiary.io (JSON:API, 20 results a page at most).
"""
import html
import re
import time

import requests

RECAP_PLUGIN = 1
NAME = "Kitsu"
VERSION = "1.0.0"
SITES = ["kitsu.app", "kitsu.io"]
LANGUAGE = "en"
NSFW = False
CHAPTERS = False                 # series' details and characters only

API = "https://kitsu.app/api/edge"
PER_PAGE = 20                    # Kitsu's most
MOST_CHARACTERS = 100
COMICS = "manga,manhwa,manhua,oneshot,doujin,oel"  # every kind but novels
SPACING = 0.5                    # seconds between requests, at least
KEEP = 3600                      # seconds an answer is kept
_session = requests.Session()
_session.headers.update({"Accept": "application/vnd.api+json", "User-Agent": "RecapStudio-Kitsu/1.0"})
_last = [0.0]
_kept: dict = {}                 # (path, params): (time, answer)


def _ask(path: str, params: dict) -> dict:
    """One request, spaced from the last and kept for an hour."""
    key = (path, tuple(sorted(params.items())))
    if key in _kept and time.time() - _kept[key][0] < KEEP:
        return _kept[key][1]
    time.sleep(max(0.0, _last[0] + SPACING - time.time()))
    _last[0] = time.time()
    r = _session.get(API + path, params=params, timeout=30)
    if r.status_code == 404:
        raise ValueError("Kitsu has no such series")
    if r.status_code in (429, 503):
        raise RuntimeError("Kitsu is busy: try again in a minute")
    r.raise_for_status()
    try:
        found = r.json()
    except ValueError:
        raise RuntimeError("Kitsu gave an answer that isn't JSON")
    _kept[key] = (time.time(), found)
    return found


def _clean(text, most: int) -> str:
    """Kitsu's text (plain, with some HTML; paragraphs sometimes run together with spaces) as plain text."""
    text = re.sub(r"<br\s*/?>|</p>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]{3,}", "\n\n", text)                                    # (paragraphs run together)
    text = re.sub(r"\[Written by [^\]]*\]|\((Source|Written by): [^)]*\)", "", text, flags=re.I)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text).strip()
    if len(text) > most:
        cut = text[:most]
        end = max(cut.rfind(". "), cut.rfind(".\n"))
        text = cut[:end + 1] if end > most // 3 else cut.rsplit(" ", 1)[0] + "…"
    return text.strip()


def _description(text) -> str:
    """A character's description, for the narrator: its first real paragraph, without "Age: 17"-style lines."""
    lines = [ln for ln in _clean(text, 4000).split("\n")
             if not re.match(r"^\s*[A-Z][\w .'/()-]{0,30}:(\s|$)", ln) or len(ln) > 120]
    paragraphs = [p.strip() for p in "\n".join(lines).split("\n\n") if p.strip()]
    sentences = [p for p in paragraphs if len(p) >= 40 or "." in p]
    return _clean((sentences or paragraphs or [""])[0], 400)


def _kind(subtype: str) -> str:
    return "webtoon" if subtype in ("manhwa", "manhua") else "comic" if subtype == "oel" else "manga"


def _card(m: dict, most: int = 600) -> dict:
    a = m["attributes"]
    titles = a.get("titles") or {}
    start = str(a.get("startDate") or "")
    return {"url": f"https://kitsu.app/manga/{a.get('slug') or m['id']}",
            "title": titles.get("en") or titles.get("en_us") or a.get("canonicalTitle") or "Untitled",
            "cover": (a.get("posterImage") or {}).get("large") or (a.get("posterImage") or {}).get("original"),
            "summary": _clean(a.get("synopsis"), most), "kind": _kind(a.get("subtype") or ""),
            "year": int(start[:4]) if start[:4].isdigit() else None}


def _id(url: str) -> str:
    """The series' number at Kitsu, from its link (by its name, as the site writes it, or its number)."""
    m = re.search(r"kitsu\.(?:app|io)/manga/([^/?#]+)", url.strip(), re.I)
    ref = m.group(1) if m else url.strip() if re.fullmatch(r"\d+", url.strip()) else None
    if not ref:
        raise ValueError("That isn't a Kitsu manga link (https://kitsu.app/manga/...)")
    if ref.isdigit():
        return ref
    found = _ask("/manga", {"filter[slug]": ref, "fields[manga]": "slug"})["data"]
    if not found:
        raise ValueError("Kitsu has no such series")
    return found[0]["id"]


def search(query: str, page: int = 1) -> dict:
    """Series whose titles match, the closest first."""
    found = _ask("/manga", {"filter[text]": query, "filter[subtype]": COMICS, "page[limit]": PER_PAGE,
                            "page[offset]": (page - 1) * PER_PAGE})
    return {"items": [_card(m, 300) for m in found.get("data") or []], "more": bool((found.get("links") or {}).get("next"))}


def series(url: str) -> dict:
    """A series' details (no chapters)."""
    return {**_card(_ask(f"/manga/{_id(url)}", {})["data"], 1500), "chapters": []}


def characters(url: str) -> list:
    """A series' characters, main ones first, then supporting."""
    mid, out, offset = _id(url), [], 0
    while len(out) < MOST_CHARACTERS:
        found = _ask(f"/manga/{mid}/characters", {"include": "character", "sort": "role", "page[limit]": PER_PAGE,
                                                  "page[offset]": offset})
        people = {c["id"]: c["attributes"] for c in found.get("included") or [] if c.get("type") == "characters"}
        for link in found.get("data") or []:
            ref = ((link.get("relationships") or {}).get("character") or {}).get("data") or {}
            c = people.get(ref.get("id"))
            if not c or not (c.get("canonicalName") or c.get("name")):
                continue
            name = (c.get("canonicalName") or c.get("name")).strip()
            others = list(c.get("otherNames") or []) + [v for v in (c.get("names") or {}).values() if v]
            out.append({"name": name, "aliases": [x for x in dict.fromkeys(others) if x and x != name],
                        "gender": "", "role": (link["attributes"].get("role") or "").lower(),
                        "description": _description(c.get("description")),
                        "picture": (c.get("image") or {}).get("original")})
        if not (found.get("links") or {}).get("next"):
            break
        offset += PER_PAGE
    return out[:MOST_CHARACTERS]
