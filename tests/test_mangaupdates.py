"""The MangaUpdates source. Without the network: it's a source of series' details (no chapters, no characters) the
app takes, its links' short ids are the API's numbers in base 36, its kinds and its descriptions' clean-up. With
--network (RECAP_TEST_NETWORK=1), through the app: a search (novels left out) and a series' details from MangaUpdates
itself."""
import importlib.util
import os

from helpers import SOURCES, App, check, done

spec = importlib.util.spec_from_file_location("mangaupdates", SOURCES / "mangaupdates.py")
mu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mu)

check("a link's short id is the series' number in base 36",
      mu._id("https://www.mangaupdates.com/series/hly6oqa/black-jack-ni-yoroshiku") == 38332508338
      and mu._id("38332508338") == 38332508338)
try:
    mu._id("https://example.com/series/1")
    check("another site's link refused", False)
except ValueError:
    check("another site's link refused", True)
check("Manga → manga, Manhwa and Manhua → webtoon, the rest comic",
      [mu._kind(k) for k in ("Manga", "Doujinshi", "Manhwa", "Manhua", "OEL", "Thai")]
      == ["manga", "manga", "webtoon", "webtoon", "comic", "comic"])
text = "A doctor's story.<BR><BR>In 2012 the author [declared it free](http://x.y/z).\n\n\n\n[i]Note:[/i] an award. (Source: MU)"
clean = mu._clean(text, 600)
check("descriptions as plain text", clean == "A doctor's story.\n\nIn 2012 the author declared it free.\n\nNote: an award.", clean)

app = App(sources=["mangaupdates"])
from recap import sources  # noqa: E402

m = {s["key"]: s for s in sources.available()}.get("plugin:mangaupdates", {})
check("MangaUpdates: search, no chapters, no characters", m.get("search") and m.get("chapters") is False
      and not m.get("characters"), m)

if os.environ.get("RECAP_TEST_NETWORK"):
    r = app.post("/api/sources/search", json={"key": "plugin:mangaupdates", "query": "Solo Leveling"})
    items = r.json().get("items") or [] if r.ok else []
    first = items[0] if items else {}
    check("a search on MangaUpdates itself", first.get("title") == "Solo Leveling" and first.get("kind") == "webtoon"
          and first.get("year") == 2018 and first.get("cover"), r.text[:300])
    check("novels left out", not any("(Novel)" in x["title"] for x in items), [x["title"] for x in items])
    found = mu.series(first.get("ref", ""))
    check("a series' details, its other names too", found["title"] == "Solo Leveling" and "Also known as" in found["summary"]
          and found["chapters"] == [], found["summary"][-200:])
else:
    print("(MangaUpdates itself is asked with --network)")
done()
