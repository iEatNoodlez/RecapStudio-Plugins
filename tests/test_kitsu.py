"""The Kitsu source. Without the network: it's a source of series' details and characters (no chapters) the app takes,
its kinds, its links, and its descriptions' clean-up (paragraphs run together, "Age: 17" lines, credits). With
--network (RECAP_TEST_NETWORK=1), through the app: a search (novels left out), a series' details and its characters
from Kitsu itself, and a webtoon it lists none for."""
import importlib.util
import os

from helpers import SOURCES, App, check, done

spec = importlib.util.spec_from_file_location("kitsu", SOURCES / "kitsu.py")
kitsu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kitsu)

check("manga, one-shots and doujinshi → manga; manhwa and manhua → webtoon; OEL → comic",
      [kitsu._kind(k) for k in ("manga", "oneshot", "doujin", "manhwa", "manhua", "oel")]
      == ["manga", "manga", "manga", "webtoon", "webtoon", "comic"])
check("a link by number needs no lookup", kitsu._id("https://kitsu.app/manga/8") == "8" and kitsu._id("8") == "8"
      and kitsu._id("https://kitsu.io/manga/8/characters") == "8")
try:
    kitsu._id("https://anilist.co/manga/8")
    check("another site's link refused", False)
except ValueError:
    check("another site's link refused", True)
text = ("Age: 17<br>Height: 165 cm<br><br>She leads the <i>guard</i> of the order, and burns what she fears.   "
        "After the eclipse, she changes. (Source: Wiki)")
desc = kitsu._description(text)
check("a character's first paragraph, without 'Age: 17' lines or credits",
      desc == "She leads the guard of the order, and burns what she fears.", desc)

app = App(sources=["kitsu"])
from recap import sources  # noqa: E402

k = {s["key"]: s for s in sources.available()}.get("plugin:kitsu", {})
check("Kitsu: search and characters, no chapters", k.get("search") and k.get("characters") and k.get("chapters") is False, k)

if os.environ.get("RECAP_TEST_NETWORK"):
    r = app.post("/api/sources/search", json={"key": "plugin:kitsu", "query": "Berserk"})
    first = (r.json().get("items") or [{}])[0] if r.ok else {}
    check("a search on Kitsu itself", first.get("title") == "Berserk" and first.get("kind") == "manga"
          and first.get("year") == 1989 and first.get("cover"), r.text[:300])
    r = app.post("/api/sources/characters", json={"key": "plugin:kitsu", "ref": first.get("ref", "")})
    cast = r.json().get("characters", []) if r.ok else []
    check("its characters: main ones first, with pictures and other names", len(cast) >= 50
          and cast[0]["role"] == "main" and all(c["picture"] for c in cast[:8])
          and any("Griffith" == c["name"] and c["aliases"] for c in cast), [(c["name"], c["role"]) for c in cast[:5]])
    found = kitsu.series(first.get("ref", ""))
    check("a series' details", found["title"] == "Berserk" and "Guts" in found["summary"] and found["chapters"] == [])
    r = app.post("/api/sources/search", json={"key": "plugin:kitsu", "query": "Solo Leveling"})
    titles = [(x["title"], x["kind"]) for x in r.json().get("items") or []] if r.ok else []
    check("a webtoon found as one, its novel left out", ("Solo Leveling", "webtoon") in titles
          and not any(t == "Only I Level Up" for t, _ in titles), titles)
    r = app.post("/api/sources/characters", json={"key": "plugin:kitsu", "ref": "https://kitsu.app/manga/tower-of-god"})
    check("a webtoon Kitsu lists no characters for: none, no error", r.ok and r.json().get("characters") == [], r.text[:200])
else:
    print("(Kitsu itself is asked with --network)")
done()
