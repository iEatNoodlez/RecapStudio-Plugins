"""The Trending finder. Without the network, with a stand-in for AniList and MangaUpdates: each site's answer as the
page gets it (ranks, types, kinds, scores, descriptions cleaned up), the filters asked for (AniList's left out rather
than null, adult series and MangaUpdates' Lolicon and Shotacon), filters a site doesn't have refused, answers kept,
MangaUpdates spaced, a busy site. With --network (RECAP_TEST_NETWORK=1), each site's real list."""
import json
import os
import time

from helpers import App, check, done

app = App(plugins=["trending", "find-series"])
check("Trending finder loads, its page script served", app.loaded("trending")
      and app.get("/plugin-web/trending/plugin.js").status_code == 200)
tr = app.module("trending")

text = "She is a ~!secret villain who!~ quiet <b>student</b>.<br>[Her club](https://x) is __chess__. (Source: Tapas)"
check("spoilers, HTML, links and the source left out", tr._clean(text, 600) == "She is a quiet student.\nHer club is chess.",
      tr._clean(text, 600))
check("Manga → manga, Manhwa and Manhua → webtoon, else comic",
      [tr._kind(t) for t in ("Manga", "Manhwa", "Manhua", "Comic")] == ["manga", "webtoon", "webtoon", "comic"])

options = app.get("/api/plugins/trending/options").json()
sites = {s["id"]: s for s in options["sites"]}
check("two sites: AniList (with finished or not), MangaUpdates (with other comics)", set(sites) == {"anilist", "mangaupdates"}
      and sites["anilist"]["status"] and not sites["mangaupdates"]["status"]
      and "other" in [k for k, _ in sites["mangaupdates"]["kinds"]] and "other" not in [k for k, _ in sites["anilist"]["kinds"]])


class Answer:
    def __init__(self, status, body):
        self.status_code, self.ok, self._body, self.headers = status, status < 400, body, {}

    def json(self):
        return self._body


asked = []  # (url, body) of each request


def anilist_media(i):
    return {"siteUrl": f"https://anilist.co/manga/{100 + i}", "title": {"english": None, "romaji": f"Romaji {i}", "native": f"네이티브 {i}"},
            "synonyms": [f"Other {i}"], "coverImage": {"large": f"https://s4.anilist.co/c{i}.jpg"},
            "description": "Ji-U is kind.<br><br>~!He dies.!~ The end.", "countryOfOrigin": "KR" if i % 2 else "JP",
            "format": "MANGA", "status": "RELEASING", "chapters": None, "genres": ["Action", "Comedy"],
            "averageScore": 85, "popularity": 47610, "isAdult": False, "startDate": {"year": 2018}}


def mu_record(i, kind="Manhwa", genres=("Action",)):
    return {"record": {"series_id": i, "title": f"Series &amp; {i}", "url": f"https://www.mangaupdates.com/series/abc{i}/x",
                       "description": "From Tapas:<br />A hero rises.<br /><br />Original Webtoon:<br />Naver",
                       "image": {"url": {"original": f"https://cdn.mangaupdates.com/image/i{i}.jpg"}}, "type": kind,
                       "year": "2024", "bayesian_rating": 7.47, "genres": [{"genre": g} for g in genres]}}


answers = {"busy": False}


def fake_post(url, json=None, timeout=None):
    asked.append((url, json, time.time()))
    if answers["busy"]:
        return Answer(429, {})
    if "anilist" in url:
        page = json["variables"]["page"]
        return Answer(200, {"data": {"Page": {"pageInfo": {"hasNextPage": page < 2},
                                              "media": [anilist_media(i) for i in range(3)]}}})
    return Answer(200, {"total_hits": 45, "results": [mu_record(i) for i in range(3)]})


tr._session.post = fake_post

r = app.get("/api/plugins/trending/list", params={"site": "anilist", "order": "trending", "kind": "manhwa"})
found = r.json() if r.ok else {}
first = (found.get("items") or [{}])[0]
sent = asked[-1][1]["variables"] if asked else {}
check("AniList: what was asked, the filters not chosen left out (not null)",
      sent == {"page": 1, "sort": ["TRENDING_DESC"], "country": "KR", "adult": False}, sent)
check("AniList: ranks, its titles, type and kind, score and members", [it["rank"] for it in found.get("items", [])] == [1, 2, 3]
      and first.get("title") == "Romaji 0" and first.get("titles") == ["Romaji 0", "네이티브 0", "Other 0"]
      and first.get("type") == "Manga" and first.get("kind") == "manga" and found["items"][1]["kind"] == "webtoon"
      and first.get("score") == "85%" and first.get("members") == 47610 and first.get("status") == "Coming out"
      and found.get("more") is True, first)
check("its cover through the app, its description without the spoiler",
      str(first.get("cover", "")).startswith("/api/sources/image/") and first.get("summary") == "Ji-U is kind.\n\nThe end.",
      first.get("summary"))

app.get("/api/plugins/trending/list", params={"site": "anilist", "order": "new", "genre": "Martial Arts",
                                              "status": "finished", "adult": "true", "page": 2})
sent = asked[-1][1]["variables"]
check("New and trending: started this past year; Martial Arts as a tag; finished; adult ones too (no isAdult)",
      sent.get("after", 0) > 20000000 and sent.get("tag") == ["Martial Arts"] and "genre" not in sent
      and sent.get("status") == "FINISHED" and "adult" not in sent and sent.get("page") == 2, sent)
r = app.get("/api/plugins/trending/list", params={"site": "anilist", "order": "new", "genre": "Martial Arts",
                                                  "status": "finished", "adult": "true", "page": 2})
check("page 2's ranks go on from 31, and the answer is kept (not asked again)",
      [it["rank"] for it in r.json()["items"]] == [31, 32, 33] and len(asked) == 2, len(asked))

r = app.get("/api/plugins/trending/list", params={"site": "mangaupdates", "order": "week", "kind": "manhwa", "genre": "Sci-Fi"})
body, item = asked[-1][1], (r.json().get("items") or [{}])[0]
check("MangaUpdates: its order, the type, the genre as it spells it, adult and Lolicon series left out",
      body.get("orderby") == "week_pos" and body.get("type") == ["Manhwa"] and body.get("genre") == ["Sci-fi"]
      and set(body.get("exclude_genre", [])) == {"Lolicon", "Shotacon", "Adult", "Hentai", "Smut"}, body)
check("MangaUpdates: its title unescaped, score out of 10, no publisher line or notes, more pages",
      item.get("title") == "Series & 0" and item.get("score") == "7.5 / 10" and item.get("summary") == "A hero rises."
      and item.get("kind") == "webtoon" and r.json().get("more") is True, item)
app.get("/api/plugins/trending/list", params={"site": "mangaupdates", "order": "year", "kind": "other", "adult": "true"})
body = asked[-1][1]
check("adult ones too: still never Lolicon or Shotacon; other comics without manga, novels or artbooks",
      body.get("exclude_genre") == ["Lolicon", "Shotacon"] and "OEL" in body["type"]
      and not {"Manga", "Novel", "Artbook", "Doujinshi"} & set(body["type"]), body)
check("MangaUpdates asked a second apart, at least", asked[-1][2] - asked[-2][2] >= 0.95, asked[-1][2] - asked[-2][2])

bad = [app.get("/api/plugins/trending/list", params=p) for p in (
    {"site": "mangadex"}, {"site": "mangaupdates", "order": "trending"}, {"site": "anilist", "kind": "other"},
    {"site": "mangaupdates", "order": "week", "status": "finished"}, {"site": "anilist", "genre": "Hentai"})]
check("refused: an unknown site, a list or kind it hasn't, finished on MangaUpdates, a genre not offered",
      [b.status_code for b in bad] == [400] * 5, [(b.status_code, b.text[:60]) for b in bad])

first_settings = app.get("/api/plugins/trending/settings").json()
saved = app.put("/api/plugins/trending/settings", json={"chapters": "get"})
refused = app.put("/api/plugins/trending/settings", json={"chapters": "everything"})
kept = json.loads((app.data / "plugin-data" / "trending" / "settings.json").read_text(encoding="utf-8"))
check("after Make a project: its chapters looked for by default; 'get' kept in its data folder, an unknown choice refused",
      first_settings == {"chapters": "show"} and saved.json() == {"chapters": "get"} and refused.status_code == 400
      and kept == {"chapters": "get"} and app.get("/api/plugins/trending/settings").json() == {"chapters": "get"},
      (first_settings, saved.text, refused.status_code, kept))

answers["busy"] = True
r = app.get("/api/plugins/trending/list", params={"site": "anilist", "order": "popular"})
check("a busy site says so", r.status_code == 503 and "try again in a minute" in r.json().get("detail", ""), r.text)

if os.environ.get("RECAP_TEST_NETWORK"):
    import requests
    tr._session.post = requests.Session().post
    tr._kept.clear()
    r = app.get("/api/plugins/trending/list", params={"site": "anilist", "order": "trending", "kind": "manhwa"})
    items = r.json().get("items", []) if r.ok else []
    check("AniList itself: 30 manhwa trending, with covers", len(items) == 30 and all(it["type"] == "Manhwa" for it in items)
          and all(it["cover"] for it in items) and not any(it["adult"] for it in items), r.text[:300])
    r = app.get("/api/plugins/trending/list", params={"site": "mangaupdates", "order": "week"})
    items = r.json().get("items", []) if r.ok else []
    check("MangaUpdates itself: 30 looked at most this week, comics only, no adult ones", len(items) == 30
          and all(it["type"] in ("Manga", "Manhwa", "Manhua", "Comic") for it in items) and not any(it["adult"] for it in items),
          json.dumps(items[:2])[:300])
else:
    print("(AniList and MangaUpdates themselves are asked with --network)")
done()
