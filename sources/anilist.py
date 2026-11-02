"""AniList: series details and characters for Recap Studio, from AniList's public API (no account or key needed).

A source plugin that gives no chapters (CHAPTERS = False): search() finds a series by its title, series() gives its
details, and characters() its characters, main ones first, each with its other names, gender, role, a short
description (spoilers left out) and a picture. Find the series uses them to name a new project and fill in its
characters. Comics, manga, manhwa and manhua are searched; light novels are left out.

AniList's API: https://docs.anilist.co (free for non-commercial use; it allows about 30 to 90 requests a minute).
"""
import html
import re
import time

import requests

RECAP_PLUGIN = 1
NAME = "AniList"
VERSION = "1.0.0"
SITES = ["anilist.co"]
LANGUAGE = "en"
NSFW = False
CHAPTERS = False                 # series' details and characters only

API = "https://graphql.anilist.co"
MOST_CHARACTERS = 100            # four pages of 25
_session = requests.Session()
_session.headers.update({"Content-Type": "application/json", "Accept": "application/json",
                         "User-Agent": "RecapStudio-AniList/1.0"})

MEDIA = """id siteUrl title { romaji english native } coverImage { large } description(asHtml: false)
           countryOfOrigin format startDate { year }"""


def _ask(query: str, variables: dict) -> dict:
    """One GraphQL request, waiting once when AniList says to slow down."""
    for attempt in range(2):
        r = _session.post(API, json={"query": query, "variables": variables}, timeout=30)
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(60, int(r.headers.get("Retry-After") or 30)))
            continue
        try:
            found = r.json()
        except ValueError:
            r.raise_for_status()
            raise RuntimeError("AniList gave an answer that isn't JSON")
        if found.get("errors"):
            message = found["errors"][0].get("message") or "an error"
            if r.status_code == 404:
                raise ValueError("AniList has no such series")
            raise RuntimeError(f"AniList answered: {message}")
        r.raise_for_status()
        return found["data"]
    raise RuntimeError("AniList is busy (too many requests): try again in a minute")


def _clean(text, most: int) -> str:
    """AniList's text (Markdown with some HTML) as plain text, with spoilers left out."""
    text = re.sub(r"~!.*?!~", "", text or "", flags=re.S)                       # spoilers
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)                          # [words](link)
    text = re.sub(r"(\*\*|__|~~)(.+?)\1", r"\2", text, flags=re.S)                 # bold, strike
    text = re.sub(r"(?<![\w*])[*_](\S(?:.*?\S)?)[*_](?![\w*])", r"\1", text)      # italics
    text = re.sub(r"\(Source: [^)]*\)", "", text, flags=re.I)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) > most:
        cut = text[:most]
        end = max(cut.rfind(". "), cut.rfind(".\n"))
        text = cut[:end + 1] if end > most // 3 else cut.rsplit(" ", 1)[0] + "…"
    return text.strip()


def _description(text) -> str:
    """A character's description, for the narrator: its first real paragraph, without the "Age: 17"-style lines
    that often start it."""
    lines = [ln for ln in _clean(text, 4000).replace("​", "").split("\n")
             if not re.match(r"^\s*[A-Z][\w .'/()-]{0,30}:(\s|$)", ln) or len(ln) > 120]
    paragraphs = [p.strip() for p in "\n".join(lines).split("\n\n") if p.strip()]
    sentences = [p for p in paragraphs if len(p) >= 40 or "." in p]  # (a stray value left from a "Guild:" line)
    return _clean((sentences or paragraphs or [""])[0], 400)


def _kind(media: dict) -> str:
    country = media.get("countryOfOrigin")
    return "manga" if country == "JP" else "webtoon" if country in ("KR", "CN", "TW") else "comic"


def _card(m: dict, most: int = 600) -> dict:
    t = m.get("title") or {}
    return {"url": m["siteUrl"], "title": t.get("english") or t.get("romaji") or t.get("native") or "Untitled",
            "cover": (m.get("coverImage") or {}).get("large"), "summary": _clean(m.get("description"), most),
            "kind": _kind(m), "year": (m.get("startDate") or {}).get("year")}


def _id(url: str) -> int:
    m = re.search(r"anilist\.co/manga/(\d+)", url) or re.fullmatch(r"(\d+)", url.strip())
    if not m:
        raise ValueError("That isn't an AniList manga link (https://anilist.co/manga/...)")
    return int(m.group(1))


def search(query: str, page: int = 1) -> dict:
    """Series whose title matches, the closest first."""
    found = _ask(f"""query ($q: String, $page: Int) {{
      Page(page: $page, perPage: 20) {{
        pageInfo {{ hasNextPage }}
        media(search: $q, type: MANGA, format_not: NOVEL, sort: SEARCH_MATCH) {{ {MEDIA} }}
      }} }}""", {"q": query, "page": page})["Page"]
    return {"items": [_card(m, 300) for m in found["media"]], "more": found["pageInfo"]["hasNextPage"]}


def series(url: str) -> dict:
    """A series' details (no chapters: AniList doesn't have them)."""
    m = _ask(f"query ($id: Int) {{ Media(id: $id, type: MANGA) {{ {MEDIA} }} }}", {"id": _id(url)})["Media"]
    return {**_card(m, 1500), "chapters": []}


def characters(url: str) -> list:
    """A series' characters, main ones first, then supporting, then background."""
    out, page = [], 1
    while len(out) < MOST_CHARACTERS:
        found = _ask("""query ($id: Int, $page: Int) {
          Media(id: $id, type: MANGA) {
            characters(page: $page, perPage: 25, sort: [ROLE, RELEVANCE, ID]) {
              pageInfo { hasNextPage }
              edges { role node { name { full native alternative } image { large } description(asHtml: false)
                                  gender } }
            } } }""", {"id": _id(url), "page": page})["Media"]["characters"]
        for edge in found["edges"]:
            node, name = edge["node"], edge["node"].get("name") or {}
            if not name.get("full"):
                continue
            picture = (node.get("image") or {}).get("large") or ""
            out.append({"name": name["full"].strip(),
                        "aliases": [a for a in (name.get("alternative") or []) + [name.get("native")] if a],
                        "gender": {"Female": "female", "Male": "male"}.get(node.get("gender"), ""),
                        "role": (edge.get("role") or "").lower(),
                        "description": _description(node.get("description")),
                        "picture": None if "/default." in picture else picture})
        if not found["pageInfo"]["hasNextPage"]:
            break
        page += 1
    return out[:MOST_CHARACTERS]
