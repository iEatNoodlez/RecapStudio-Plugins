"""Trending finder, a Recap Studio feature plugin: the series trending now, to pick the next one to recap.

Two lists, from public APIs with no account or key:
- AniList (https://docs.anilist.co): what its members are reading and adding most these days ("trending now"), the
  new series among them, and the most popular and best rated of all. It can be narrowed to manga (from Japan), manhwa
  (Korea) or manhua (China), a genre, and series still coming out or finished.
- MangaUpdates (https://api.mangaupdates.com): the series whose pages were looked at most this week, month, three
  months or year. Its readers mostly read scanlations, so it leans to manhwa and to what's being translated now.
Adult series are left out unless asked for. Series MangaUpdates marks Lolicon or Shotacon always are.

The page script (web/plugin.js) shows them in a dialog from the sidebar, marks the ones a project is already of, and
opens New project for one (Find the series then picks it from the same site, with its characters). Once the project
is made, it looks for its chapters in the member's own sources (Get chapters from a site, searching them all), or
gets them straight away when one has a series called that: the setting kept in settings.json, in its data folder.

Answers are kept for a while (AniList's 15 minutes, MangaUpdates' an hour, as its policy asks), and MangaUpdates is
asked at most once a second.
"""
import html
import json
import re
import threading
import time
from datetime import date

import requests
from fastapi import Body, HTTPException

from recap import sources

PER_PAGE = 30
ANILIST = "https://graphql.anilist.co"
MANGAUPDATES = "https://api.mangaupdates.com/v1/series/search"
KEEP = {"anilist": 15 * 60, "mangaupdates": 3600}  # seconds an answer is kept
SPACING = 1.0                                      # seconds between MangaUpdates requests, at least

GENRES = ["Action", "Adventure", "Comedy", "Drama", "Fantasy", "Horror", "Martial Arts", "Mystery", "Psychological",
          "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural"]
ANILIST_TAGS = {"Martial Arts"}                    # a tag on AniList, not a genre
MU_GENRE = {"Sci-Fi": "Sci-fi"}                    # MangaUpdates' spelling
MU_ADULT = ["Adult", "Hentai", "Smut"]
MU_NOTES = re.compile(r"\n\s*(?:Original (?:Web ?toon|Web ?comic|Web ?novel|Novel|Manhwa|Manhua|Work)|"
                      r"Official (?:Translations?|English)|Links?|Notes?|Raws?)\b[^\n:]{0,30}(?::|\s*(?:\n|$))", re.I)
MU_NEVER = ["Lolicon", "Shotacon"]
MU_TYPES = {"manga": ["Manga"], "manhwa": ["Manhwa"], "manhua": ["Manhua"],
            "other": ["OEL", "Thai", "Indonesian", "Vietnamese", "Filipino", "Malaysian", "French", "Spanish", "Nordic"]}
MU_TYPES["all"] = [t for k in ("manga", "manhwa", "manhua", "other") for t in MU_TYPES[k]]  # (no novels, artbooks…)

KINDS = [["all", "All kinds"], ["manga", "Manga (Japan)"], ["manhwa", "Manhwa (Korea)"], ["manhua", "Manhua (China)"]]
SITES = {
    "anilist": {"name": "AniList", "status": True, "kinds": KINDS,
                "orders": [["trending", "Trending now"], ["new", "New and trending"], ["popular", "Most popular"],
                           ["top", "Best rated"]],
                "about": "What AniList's members are reading and adding to their lists most these days. New and "
                         "trending: the ones among them that started this past year."},
    "mangaupdates": {"name": "MangaUpdates", "status": False, "kinds": KINDS + [["other", "Other comics"]],
                     "orders": [["week", "Looked at most this week"], ["month", "Looked at most this month"],
                                ["quarter", "Looked at most these three months"], ["year", "Looked at most this year"]],
                     "about": "The series whose pages MangaUpdates' readers looked at most. They mostly read "
                              "scanlations, so it leans to manhwa and to what's being translated now."},
}
ANILIST_SORT = {"trending": "TRENDING_DESC", "new": "TRENDING_DESC", "popular": "POPULARITY_DESC", "top": "SCORE_DESC"}
MU_ORDER = {"week": "week_pos", "month": "month1_pos", "quarter": "month3_pos", "year": "year_pos"}
COUNTRY = {"manga": "JP", "manhwa": "KR", "manhua": "CN"}
TYPE = {"JP": "Manga", "KR": "Manhwa", "CN": "Manhua", "TW": "Manhua"}  # AniList's country as the series' type
STATUS = {"RELEASING": "Coming out", "FINISHED": "Finished", "HIATUS": "On hiatus", "CANCELLED": "Cancelled",
          "NOT_YET_RELEASED": "Not out yet"}

QUERY = """query ($page: Int, $sort: [MediaSort], $country: CountryCode, $genre: [String], $tag: [String],
                  $status: MediaStatus, $adult: Boolean, $after: FuzzyDateInt) {
  Page(page: $page, perPage: %d) {
    pageInfo { hasNextPage }
    media(type: MANGA, format_not: NOVEL, sort: $sort, countryOfOrigin: $country, genre_in: $genre, tag_in: $tag,
          status: $status, isAdult: $adult, startDate_greater: $after) {
      siteUrl title { english romaji native } synonyms coverImage { large } description(asHtml: false)
      countryOfOrigin format status chapters genres averageScore popularity isAdult startDate { year }
    } } }""" % PER_PAGE

_session = requests.Session()
_session.headers.update({"Accept": "application/json", "User-Agent": "RecapStudio-Trending/1.0"})
_lock = threading.Lock()
_kept: dict = {}                                   # (site, filters…): (time, answer)
_last = [0.0]                                      # when MangaUpdates was last asked
CHAPTERS = ("show", "get", "off")  # after Make a project: its chapters looked for and shown, got, or neither
_plugin = None


def _clean(text, most: int) -> str:
    """A description (AniList's Markdown with some HTML; MangaUpdates' HTML, Markdown links and BBCode) as plain
    text, without spoilers, cut at a sentence."""
    text = re.sub(r"~!.*?!~", "", text or "", flags=re.S)                       # AniList's spoilers
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)                          # [words](link)
    text = re.sub(r"\[url=[^\]]*\]([^\[]*)\[/url\]", r"\1", text, flags=re.I)     # [url=link]words[/url]
    text = re.sub(r"\[/?(b|i|u|s|url|spoiler)\]", "", text, flags=re.I)
    text = re.sub(r"(\*\*|__|~~)(.+?)\1", r"\2", text, flags=re.S)                 # bold, strike
    text = re.sub(r"\(Source: [^)]*\)", "", text, flags=re.I)
    text = re.sub(r" *\n *", "\n", re.sub(r"[ \t]+", " ", text))
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text).strip()
    if len(text) > most:
        cut = text[:most]
        end = max(cut.rfind(". "), cut.rfind(".\n"))
        text = cut[:end + 1] if end > most // 3 else cut.rsplit(" ", 1)[0] + "…"
    return text.strip()


def _kind(series_type: str) -> str:
    """The series' type as the app's kind of comic, for New project: manga, webtoon (manhwa, manhua) or comic."""
    return "manga" if series_type == "Manga" else "webtoon" if series_type in ("Manhwa", "Manhua") else "comic"


def _anilist_item(m: dict) -> dict:
    t = m.get("title") or {}
    titles = [x for x in (t.get("english"), t.get("romaji"), t.get("native"), *(m.get("synonyms") or [])) if x]
    series_type = TYPE.get(m.get("countryOfOrigin"), "Comic")
    score = m.get("averageScore")
    return {"url": m.get("siteUrl") or "", "title": titles[0] if titles else "Untitled",
            "titles": list(dict.fromkeys(titles))[:20], "cover": sources.image_link((m.get("coverImage") or {}).get("large")),
            "summary": _clean(m.get("description"), 700), "type": series_type, "kind": _kind(series_type),
            "one_shot": m.get("format") == "ONE_SHOT", "year": (m.get("startDate") or {}).get("year"),
            "status": STATUS.get(m.get("status"), ""), "chapters": m.get("chapters"), "genres": (m.get("genres") or [])[:6],
            "score": f"{score}%" if score else "", "members": m.get("popularity") or 0, "adult": bool(m.get("isAdult"))}


def _mu_item(m: dict) -> dict:
    genres = [g.get("genre") for g in m.get("genres") or [] if g.get("genre")]
    series_type = m.get("type") if m.get("type") in ("Manga", "Manhwa", "Manhua") else "Comic"
    year, score = str(m.get("year") or ""), m.get("bayesian_rating")
    title = html.unescape(m.get("title") or "") or "Untitled"
    summary = re.sub(r"^From [^:\n]{1,40}:\s*\n", "", _clean(m.get("description"), 4000))  # ("From Tapas:", its publisher)
    summary = MU_NOTES.split(summary, maxsplit=1)[0]  # (where it was first published, its translations, links)
    return {"url": m.get("url") or "", "title": title, "titles": [title],
            "cover": sources.image_link(((m.get("image") or {}).get("url") or {}).get("original")),
            "summary": _clean(summary, 700), "type": series_type, "kind": _kind(series_type),
            "one_shot": False, "year": int(year[:4]) if year[:4].isdigit() else None, "status": "", "chapters": None,
            "genres": ["Sci-Fi" if g == "Sci-fi" else g for g in genres][:6],
            "score": f"{score:.1f} / 10" if score else "", "members": 0, "adult": any(g in MU_ADULT for g in genres)}


def _post(site: str, url: str, body: dict) -> dict:
    name = SITES[site]["name"]
    try:
        r = _session.post(url, json=body, timeout=30)
    except requests.RequestException as e:
        raise HTTPException(502, f"Couldn't reach {name}: {e.__class__.__name__}")
    if r.status_code in (429, 503):
        raise HTTPException(503, f"{name} is busy (too many requests): try again in a minute")
    try:
        found = r.json()
    except ValueError:
        raise HTTPException(502, f"{name} gave an answer that isn't JSON (HTTP {r.status_code})")
    if site == "anilist" and found.get("errors"):
        raise HTTPException(502, f"AniList answered: {found['errors'][0].get('message') or 'an error'}")
    if not r.ok:
        raise HTTPException(502, f"{name} answered HTTP {r.status_code}")
    return found


def _anilist(order: str, kind: str, genre: str, status: str, adult: bool, page: int) -> dict:
    after = None
    if order == "new":
        today = date.today()
        after = (today.year - 1) * 10000 + today.month * 100 + today.day  # (a FuzzyDateInt: YYYYMMDD)
    filters = {"page": page, "sort": [ANILIST_SORT[order]], "country": COUNTRY.get(kind),
               "genre": [genre] if genre and genre not in ANILIST_TAGS else None,
               "tag": [genre] if genre in ANILIST_TAGS else None,
               "status": status.upper() if status else None, "adult": None if adult else False, "after": after}
    found = _post("anilist", ANILIST, {"query": QUERY, "variables": {  # (left out, not null: AniList's null matches nothing)
        k: v for k, v in filters.items() if v is not None}})["data"]["Page"]
    return {"items": [_anilist_item(m) for m in found["media"]], "more": bool(found["pageInfo"]["hasNextPage"])}


def _mangaupdates(order: str, kind: str, genre: str, adult: bool, page: int) -> dict:
    body = {"orderby": MU_ORDER[order], "perpage": PER_PAGE, "page": page, "type": MU_TYPES[kind],
            "exclude_genre": MU_NEVER + ([] if adult else MU_ADULT)}
    if genre:
        body["genre"] = [MU_GENRE.get(genre, genre)]
    time.sleep(max(0.0, _last[0] + SPACING - time.time()))
    _last[0] = time.time()
    found = _post("mangaupdates", MANGAUPDATES, body)
    return {"items": [_mu_item(x.get("record") or {}) for x in found.get("results") or []],
            "more": page * PER_PAGE < (found.get("total_hits") or 0)}


def trending(site: str, order: str, kind: str = "all", genre: str = "", status: str = "", adult: bool = False,
             page: int = 1) -> dict:
    """A page of a site's list, each series with its place in it: {"items": [{"rank", "url", "title", "titles",
    "cover", "summary", "type", "kind", "one_shot", "year", "status", "chapters", "genres", "score", "members",
    "adult"}], "more"}."""
    about = SITES.get(site)
    if not about:
        raise HTTPException(400, f"No list from {site}")
    if order not in {o for o, _ in about["orders"]} or kind not in {k for k, _ in about["kinds"]}:
        raise HTTPException(400, f"{about['name']} has no such list")
    if genre and genre not in GENRES:
        raise HTTPException(400, f"{genre} isn't a genre this lists")
    if status not in ("", "releasing", "finished") or status and not about["status"]:
        raise HTTPException(400, f"{about['name']} can't tell which series are finished")
    page = max(1, min(int(page), 20))
    key = (site, order, kind, genre, status, bool(adult), page)
    with _lock:  # (one request at a time: MangaUpdates asks for that, and AniList allows few a minute)
        kept = _kept.get(key)
        if kept and time.time() - kept[0] < KEEP[site]:
            return kept[1]
        found = (_anilist(order, kind, genre, status, adult, page) if site == "anilist"
                 else _mangaupdates(order, kind, genre, adult, page))
        for i, it in enumerate(found["items"]):
            it["rank"] = (page - 1) * PER_PAGE + i + 1
        _kept[key] = (time.time(), found)
        return found


def _settings() -> dict:
    try:
        kept = json.loads((_plugin.data_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        kept = {}
    return {"chapters": kept.get("chapters") if kept.get("chapters") in CHAPTERS else "show"}


def setup(plugin):
    global _plugin
    _plugin = plugin

    @plugin.router.get("/settings")
    def settings():
        return _settings()

    @plugin.router.put("/settings")
    def save_settings(chapters: str = Body(..., embed=True)):
        if chapters not in CHAPTERS:
            raise HTTPException(400, f"{chapters} isn't one of {', '.join(CHAPTERS)}")
        plugin.data_dir.mkdir(parents=True, exist_ok=True)
        (plugin.data_dir / "settings.json").write_text(json.dumps({"chapters": chapters}), encoding="utf-8")
        return _settings()

    @plugin.router.get("/options")
    def options():
        return {"sites": [{"id": k, **v} for k, v in SITES.items()], "genres": GENRES}

    @plugin.router.get("/list")
    def listed(site: str = "anilist", order: str = "trending", kind: str = "all", genre: str = "", status: str = "",
               adult: bool = False, page: int = 1):
        return trending(site, order, kind, genre, status, adult, page)
