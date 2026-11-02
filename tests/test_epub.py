"""EPUB comics, on made-up EPUBs (pictures of different sizes, so their order can be checked):
- an EPUB 3 volume split into its chapters by its contents (a volume's entry over its first chapter, the cover with
  chapter 1, the contents and copyright pages left out), its pages found however they're written (<img>, SVG's
  <image>, a %20 in a name, an HTML entity), links out of the EPUB and missing pictures passed over;
- an EPUB 2 volume (its contents in toc.ncx, after the book's title), with its divider pages (one picture under three
  names) and a preview of the next volume left out;
- one without chapters in its contents, and one whose contents list every page: one chapter, named after the file;
- left out, saying why: DRM, a novel, a file that isn't a zip, a zip that isn't an EPUB (scrambled fonts are fine);
- through the app: Import folder, dropped files, and the Watched folder (a part renamed isn't added again);
- with RECAP_TEST_EPUB=<a real EPUB of yours>, that one too, through Import folder (into this test's own folder)."""
import io
import os
import zipfile
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from helpers import App, check, done

app = App(plugins=["epub-comics", "watched-folder"])
store = app.store
from recap import importer  # noqa: E402

check("both load", app.loaded("epub-comics") and app.loaded("watched-folder"))
check("the app reads .epub with it", importer.FILE_TYPES.get(".epub", {}).get("name") == "EPUB"
      and {"ext": ".epub", "name": "EPUB"} in app.get("/api/plugins").json()["file_types"])
ec = app.module("epub-comics")


def picture(w: int, h: int = 300) -> bytes:
    return cv2.imencode(".jpg", np.full((h, w, 3), 128, np.uint8))[1].tobytes()


def page(src: str, svg: bool = False, extra: str = "") -> str:
    if svg:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 800 1200">'
                f'<image width="800" height="1200" xlink:href="{src}"/></svg>{extra}')
    return f'<div class="page"><img src="{src}" alt="page"/></div>{extra}'


def make_epub(docs, pictures, toc=(), ncx=False, encrypted=(), container=True) -> bytes:
    """docs: [(name, body)] in reading order (OEBPS/Text/<name>.xhtml); pictures: {name: bytes} (OEBPS/Images/);
    toc: [(title, doc name, [children])]; encrypted: the files META-INF/encryption.xml lists."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        if container:
            z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                       'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                       '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                       '</rootfiles></container>')
        if encrypted:
            z.writestr("META-INF/encryption.xml", '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
                       'xmlns:enc="http://www.w3.org/2001/04/xmlenc#">' + "".join(
                           f'<enc:EncryptedData><enc:CipherData><enc:CipherReference URI="{u}"/></enc:CipherData>'
                           '</enc:EncryptedData>' for u in encrypted) + "</encryption>")
        items = [f'<item id="{n}" href="Text/{n}.xhtml" media-type="application/xhtml+xml"/>' for n, _ in docs]
        items += [f'<item id="img{i}" href="Images/{n.replace(" ", "%20")}" media-type="image/jpeg"/>'
                  for i, n in enumerate(pictures)]
        for n, body in docs:
            z.writestr(f"OEBPS/Text/{n}.xhtml", '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" '
                       f'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>{n}</title><style>p {{ margin: 0 }}</style>'
                       f'</head><body>{body}</body></html>')
        for n, data in pictures.items():
            z.writestr(f"OEBPS/Images/{n}", data)

        def nav(entries):
            return "<ol>" + "".join(f'<li><a href="{d}.xhtml">{t}</a>{nav(c) if c else ""}</li>' for t, d, c in entries) + "</ol>"

        def points(entries):
            return "".join(f'<navPoint id="n{t}"><navLabel><text>{t}</text></navLabel><content src="Text/{d}.xhtml"/>'
                           f'{points(c)}</navPoint>' for t, d, c in entries)
        if ncx:
            items.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
            z.writestr("OEBPS/toc.ncx", '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
                       f'version="2005-1"><docTitle><text>Test volume</text></docTitle><docAuthor><text>Someone</text></docAuthor>'
                       f'<navMap>{points(toc)}</navMap></ncx>')
        else:
            items.append('<item id="nav" href="Text/nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
            first = docs[0][0] if docs else "x"
            z.writestr("OEBPS/Text/nav.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                       f'<body><nav epub:type="landmarks"><ol><li><a epub:type="bodymatter" href="{first}.xhtml">Begin Reading</a>'
                       f'</li></ol></nav><nav epub:type="toc" id="toc"><h1>Contents</h1>{nav(toc)}</nav></body></html>')
        spine = "".join(f'<itemref idref="{n}"/>' for n, _ in docs)
        toc_attr = ' toc="ncx"' if ncx else ""
        z.writestr("OEBPS/content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Test</dc:title></metadata>'
                   f'<manifest>{"".join(items)}</manifest><spine{toc_attr}>{spine}</spine></package>')
    return out.getvalue()


# ---- the EPUBs ----
pics = {"cover.jpg": picture(100), "copyright.jpg": picture(101)}
docs = [("cover", page("../Images/cover.jpg")),
        ("contents", "<h1>Contents</h1><p>Chapter 1 &hellip; 3</p><p>Chapter 2 &hellip; 8</p>")]
for i in range(1, 6):  # chapter 1: pictures 201 to 205 wide
    name = f"page {i:03d}.jpg" if i == 3 else f"c1_{i}.jpg"
    pics[name] = picture(200 + i)
    src = "../Images/" + name.replace(" ", "%20")
    docs.append((f"c1_{i}", page(src, svg=i == 2, extra="<p>&nbsp;</p>" if i == 4 else "")))
for i in range(1, 5):  # chapter 2: 301 to 304
    pics[f"c2_{i}.jpg"] = picture(300 + i)
    extra = ('<img src="http://example.com/ad.jpg"/>' if i == 4 else "") + ('<img src="../Images/missing.jpg"/>' if i == 2 else "")
    docs.append((f"c2_{i}", page(f"../Images/c2_{i}.jpg", extra=extra)))
docs.append(("copyright", page("../Images/copyright.jpg")))
VOL1 = make_epub(docs, pics, toc=[("Cover", "cover", []), ("Contents", "contents", []),
                                  ("Volume 1", "c1_1", [("Chapter 1", "c1_1", []), ("Chapter 2", "c2_1", [])]),
                                  ("Copyright", "copyright", [])])

pics2, docs2 = {}, []
DIVIDER = picture(950)
for ch, dividers in ((3, ["div1", "div2"]), (4, ["div3"])):
    for i in range(1, 5):
        pics2[f"c{ch}_{i}.jpg"] = picture(ch * 100 + 100 + i)  # 401 to 404, 501 to 504
        docs2.append((f"c{ch}_{i}", page(f"../Images/c{ch}_{i}.jpg", svg=True)))
    for d in dividers:  # (the same picture each time, under its own name)
        pics2[f"{d}.jpg"] = DIVIDER
        docs2.append((d, page(f"../Images/{d}.jpg")))
for i in (1, 2):
    pics2[f"preview{i}.jpg"] = picture(900 + i)
    docs2.append((f"preview{i}", page(f"../Images/preview{i}.jpg")))
VOL2 = make_epub(docs2, pics2, ncx=True,
                 toc=[("Chapter 3", "c3_1", []), ("Chapter 4", "c4_1", []), ("Preview of Volume 3", "preview1", [])])


def plain(n: int, toc=False, **kw) -> bytes:
    p = {f"p{i}.jpg": picture(600 + i) for i in range(1, n + 1)}
    d = [(f"p{i}", page(f"../Images/p{i}.jpg")) for i in range(1, n + 1)]
    return make_epub(d, p, toc=[(f"Page {i}", f"p{i}", []) for i in range(1, n + 1)] if toc else (), **kw)


text = "<p>" + "It was a dark and stormy night; the rain fell in torrents. " * 20 + "</p>"
NOVEL = make_epub([(f"ch{i}", f"<h1>Chapter {i}</h1>{text}") for i in range(1, 7)] + [("art", page("../Images/art.jpg"))],
                  {"art.jpg": picture(700)}, toc=[(f"Chapter {i}", f"ch{i}", []) for i in range(1, 7)])
files = {"Vol 01.epub": VOL1, "Vol 02.epub": VOL2, "Oneshot.epub": plain(5), "Pages.epub": plain(8, toc=True),
         "Locked.epub": plain(3, encrypted=["OEBPS/Images/p1.jpg", "OEBPS/Fonts/a.otf"]),
         "Fonts.epub": plain(2, encrypted=["OEBPS/Fonts/a.otf"]), "Novel.epub": NOVEL, "Fake.epub": b"not a zip at all",
         "No container.epub": plain(2, container=False)}
series = app.data / "comics" / "My Series"
series.mkdir(parents=True)
for name, data in files.items():
    (series / name).write_bytes(data)

# ---- what the plugin finds ----
parts = ec.chapters(series / "Vol 01.epub")
check("EPUB 3: its chapters, by its contents (the chapter's name over the volume's)",
      [x["name"] for x in parts] == ["Chapter 1", "Chapter 2"], parts)
check("the cover with chapter 1; the contents page (text) and the copyright page left out",
      [len(x["pages"]) for x in parts] == [6, 4] and parts[0]["pages"][0].endswith("cover.jpg")
      and not any("copyright" in p for x in parts for p in x["pages"]), parts)
check("pages however they're written: <img>, SVG's <image>, %20, an entity",
      parts[0]["pages"][1:] == ["OEBPS/Images/c1_1.jpg", "OEBPS/Images/c1_2.jpg", "OEBPS/Images/page 003.jpg",
                                "OEBPS/Images/c1_4.jpg", "OEBPS/Images/c1_5.jpg"], parts[0]["pages"])
check("links out of the EPUB and missing pictures passed over", parts[1]["pages"] == [f"OEBPS/Images/c2_{i}.jpg" for i in range(1, 5)],
      parts[1]["pages"])
got = list(ec.pages(series / "Vol 01.epub", parts[0]["pages"]))
check("its pictures read, as they are", [n for n, _ in got] == parts[0]["pages"] and got[3][1] == pics["page 003.jpg"])
parts = ec.chapters(series / "Vol 02.epub")
check("EPUB 2 (toc.ncx): its chapters (not the book's title with the first); its dividers and the preview left out",
      [(x["name"], len(x["pages"])) for x in parts] == [("Chapter 3", 4), ("Chapter 4", 4)], parts)
check("no chapters in its contents: one chapter", [(x["name"], len(x["pages"])) for x in ec.chapters(series / "Oneshot.epub")]
      == [(None, 5)])
check("contents listing every page: one chapter", [(x["name"], len(x["pages"])) for x in ec.chapters(series / "Pages.epub")]
      == [(None, 8)])
check("scrambled fonts aren't DRM", len(ec.chapters(series / "Fonts.epub")) == 1)


def why(name: str) -> str:
    try:
        ec.chapters(series / name)
    except ValueError as e:
        return str(e)
    return "(read)"


check("DRM: left out, saying so", "DRM" in why("Locked.epub") and "DRM-free" in why("Locked.epub"), why("Locked.epub"))
check("a novel: left out, saying so", "a book of text" in why("Novel.epub"), why("Novel.epub"))
check("not a zip", why("Fake.epub") == "it isn't an EPUB (it isn't a zip file)", why("Fake.epub"))
check("not an EPUB", why("No container.epub") == "it isn't an EPUB (it has no META-INF/container.xml)", why("No container.epub"))

# ---- through the app ----
pid = app.project("My Series")
r = app.post(f"/api/projects/{pid}/scan-folder", json={"path": str(series)}).json()
check("Import folder: each volume's chapters, in order, and each other file as one",
      [(c["name"], c["pages"]) for c in r["chapters"]]
      == [("Fonts", 2), ("Oneshot", 5), ("Pages", 8), ("Chapter 1", 6), ("Chapter 2", 4), ("Chapter 3", 4), ("Chapter 4", 4)],
      r["chapters"])
check("tagged EPUB", all(c.get("type") == "EPUB" for c in r["chapters"]))
check("and what's left out, with why", sorted(x["file"] for x in r["skipped"])
      == ["Fake.epub", "Locked.epub", "No container.epub", "Novel.epub"], r["skipped"])
r = app.post(f"/api/projects/{pid}/import-folder",
             json={"path": str(series), "keys": [c["key"] for c in r["chapters"] if c["name"].startswith("Chapter")]}).json()
check("imported", r["chapters"] == 4 and r["pages"] == 18, r)
p = store.load(pid)
widths = {c["name"]: [x["w"] for x in p["pages"] if x["chapter"] == c["id"]] for c in p["chapters"]}
check("each page, in order", widths == {"Chapter 1": [100, 201, 202, 203, 204, 205], "Chapter 2": [301, 302, 303, 304],
                                        "Chapter 3": [401, 402, 403, 404], "Chapter 4": [501, 502, 503, 504]}, widths)

dropped = app.project("Dropped")
r = app.post(f"/api/projects/{dropped}/pages", files=[("files", ("Vol 02.epub", VOL2)), ("files", ("Novel.epub", NOVEL))]).json()
check("dropped on a project: its chapters", r["added"] == 8
      and [c["name"] for c in store.load(dropped)["chapters"]] == ["Chapter 3", "Chapter 4"], r)
check("and the novel left out, saying so", [x["file"] for x in r["skipped"]] == ["Novel.epub"], r)

wf = app.module("watched-folder")
watched = app.data / "watched"
(watched / "Watched series").mkdir(parents=True)
(watched / "Watched series" / "Vol 01.epub").write_bytes(VOL1)
w = app.project("Watched series")
app.put("/api/plugins/watched-folder/settings", json={"folder": str(watched), "recap": False, "create": False})
wf.SETTLE = 0
wf.look()
check("the Watched folder: a volume's chapters added", [c["name"] for c in store.load(w)["chapters"]] == ["Chapter 1", "Chapter 2"])
store.update(w, lambda p: p["chapters"][0].update(name="Chapter 1: Renamed"))
wf.look()
check("not again, even with one renamed (each part is a source of its own)", len(store.load(w)["chapters"]) == 2,
      [c["name"] for c in store.load(w)["chapters"]])

if os.environ.get("RECAP_TEST_EPUB"):  # (a real EPUB: read where it is, imported into this test's own data folder)
    book = Path(os.environ["RECAP_TEST_EPUB"])
    real = app.project("A real EPUB")
    r = app.post(f"/api/projects/{real}/scan-folder", json={"path": str(book.parent)}).json()
    mine = [c for c in r["chapters"] if c["key"].split("#")[0] == book.stem]
    for c in mine:
        print(f"   {c['name']}: {c['pages']} pages")
    check("a real EPUB: its chapters found", mine and not any(x["file"] == book.name for x in r["skipped"]), r["skipped"])
    r = app.post(f"/api/projects/{real}/import-folder", json={"path": str(book.parent), "keys": [c["key"] for c in mine]}).json()
    check("and imported, every page", r["chapters"] == len(mine) and r["pages"] == sum(c["pages"] for c in mine), r)
    p = store.load(real)
    times = Counter(store.page_path(p, x).read_bytes() for x in p["pages"])
    check("no picture three times or more (dividers left out)", max(times.values()) < 3, max(times.values()))
done()
