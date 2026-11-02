"""The AniList source and Find the series. Without the network: AniList is a source of series' details (no chapters,
with characters) the app takes, and its text clean-up leaves out spoilers and "Age: 17" lines. With --network
(RECAP_TEST_NETWORK=1), through the app: a search, a series' details and its characters from AniList itself."""
import importlib.util
import os

from helpers import SOURCES, App, check, done

spec = importlib.util.spec_from_file_location("anilist", SOURCES / "anilist.py")
anilist = importlib.util.module_from_spec(spec)
spec.loader.exec_module(anilist)

text = "__Age:__ 17\n__Height:__ 165 cm\n\nShe is a ~!secret villain who!~ quiet student at Seirin. [Her club](https://x) is chess."
desc = anilist._description(text)
check("spoilers, links and 'Age: 17' lines left out", desc == "She is a quiet student at Seirin. Her club is chess.", desc)
check("Japan → manga, Korea → webtoon, else comic", [anilist._kind({"countryOfOrigin": c}) for c in ("JP", "KR", "CN", "US")]
      == ["manga", "webtoon", "webtoon", "comic"])
check("a link or an id", anilist._id("https://anilist.co/manga/31668/Black-Jack") == 31668 and anilist._id("31668") == 31668)

app = App(plugins=["find-series"], sources=["anilist"])
from recap import sources  # noqa: E402

a = {s["key"]: s for s in sources.available()}.get("plugin:anilist", {})
check("AniList: search and characters, no chapters", a.get("search") and a.get("characters") and a.get("chapters") is False, a)
check("Find the series loads, its page script served", app.loaded("find-series")
      and app.get("/plugin-web/find-series/plugin.js").status_code == 200)

if os.environ.get("RECAP_TEST_NETWORK"):
    r = app.post("/api/sources/search", json={"key": "plugin:anilist", "query": "Black Jack ni Yoroshiku"})
    first = (r.json().get("items") or [{}])[0] if r.ok else {}
    check("a search on AniList itself", first.get("year") == 2002 and first.get("kind") == "manga" and first.get("cover"), r.text[:300])
    r = app.post("/api/sources/characters", json={"key": "plugin:anilist", "ref": first.get("ref", "")})
    cast = r.json().get("characters", []) if r.ok else []
    check("its characters, with pictures", cast and cast[0]["name"] == "Eijirou Saito" and cast[0]["picture"], cast[:1])
else:
    print("(AniList itself is asked with --network)")
done()
