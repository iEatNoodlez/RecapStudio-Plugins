"""EPUB comics, a Recap Studio feature plugin: chapters imported from .epub files wherever the app imports them (Import
folder, files dropped on a project, the Watched folder plugin), as from .cbz files.

A comic's EPUB (a volume, say) is read in its reading order (its spine), each page's picture taken as it is. When its
table of contents names two chapters or more, it becomes one chapter of the project each, named as the contents name
them; the cover, title and colour pages before the first chapter go with it, and the contents, copyright and
advertisement pages are left out. Otherwise the whole book is one chapter, named after the file.

Left out, saying why: an EPUB locked with DRM (its pages encrypted, to open only in its store's app), and a novel (pages
of text, not pictures). Only the EPUB's own files are read, never anything it links to.
"""
import html
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

PICTURES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
FONTS = {".ttf", ".otf", ".woff", ".woff2"}  # (an EPUB may scramble its fonts; that isn't DRM on its pages)
MAX_DOC = 4 << 20          # a page's XHTML (or the contents), at most
MAX_PICTURE = 100 << 20    # a page's picture
TEXT_PAGE = 300            # characters on a page without pictures that make it a page of text
SHORTEST = 4               # chapters shorter than this on average: the contents list pages, not chapters

# contents entries that aren't the story (what they start with): their pages are left out
LEAVE_OUT = re.compile(r"(table of contents|contents|toc|copyright|colophon|newsletter|sign up|also available|also by|"
                       r"about the (author|artist|publisher|creator)s?|ads|advertisements?|more (from|books|titles)|"
                       r"other (books|titles)|preview)\b")
# contents entries before the story (pictures still): with the chapter after them
FRONT = re.compile(r"(front )?cover|title( page)?|front ?matter|frontispiece|insert|"
                   r"colou?r (pages?|inserts?|illustrations?)|illustrations?")

TAG = r"<(?:[\w-]+:)?{}\b[^>]*>"
PICTURE_TAG = re.compile(TAG.format("(?:img|image)"), re.I)
LINK = re.compile(r"<(?:[\w-]+:)?a\b[^>]*?\bhref\s*=\s*([\"'])(.*?)\1[^>]*>(.*?)</(?:[\w-]+:)?a\s*>", re.S | re.I)
TOC_NAV = re.compile(r"<(?:[\w-]+:)?nav\b[^>]*?\btype\s*=\s*([\"'])[^\"']*\btoc\b[^\"']*\1[^>]*>(.*?)</(?:[\w-]+:)?nav\s*>",
                     re.S | re.I)
NCX_POINT = re.compile(r"<(?:[\w-]+:)?text\b[^>]*>(.*?)</(?:[\w-]+:)?text\s*>\s*</(?:[\w-]+:)?navLabel\s*>\s*"
                       r"<(?:[\w-]+:)?content\b[^>]*?\bsrc\s*=\s*([\"'])(.*?)\2", re.S | re.I)


def _tags(text: str, name: str):
    return (m.group() for m in re.finditer(TAG.format(name), text, re.I))


def _attr(tag: str, name: str) -> str | None:
    m = re.search(r"(?:^|\s)" + re.escape(name) + r"\s*=\s*([\"'])(.*?)\1", tag, re.S | re.I)
    return html.unescape(m.group(2)) if m else None


def _text(z: zipfile.ZipFile, name: str) -> str:
    """A text file in the EPUB (KeyError when it isn't there)."""
    info = z.getinfo(name)
    if info.file_size > MAX_DOC:
        raise KeyError(name)
    data = z.read(info)
    return data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8", "replace")


def _join(base: str, href: str) -> str | None:
    """A link's file in the EPUB, from the folder of the file it's in."""
    href = unquote(href.split("#")[0].split("?")[0].strip())
    if not href or re.match(r"[a-z][a-z0-9+.-]*:", href, re.I):  # (data:, http: and the like: not in the EPUB)
        return None
    return posixpath.normpath(posixpath.join(base, href)).lstrip("/")


def _plain(markup: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def _key(title: str) -> str:
    return " ".join(re.sub(r"[^\w ]+", " ", title.lower()).split())


def _book(z: zipfile.ZipFile):
    """Its spine (the pages' documents, in reading order), table of contents documents (EPUB 3's, EPUB 2's)."""
    try:
        container = _text(z, "META-INF/container.xml")
    except KeyError:
        raise ValueError("it isn't an EPUB (it has no META-INF/container.xml)")
    root = next((_attr(t, "full-path") for t in _tags(container, "rootfile") if _attr(t, "full-path")), None)
    try:
        opf = _text(z, root or "")
    except KeyError:
        raise ValueError("it isn't an EPUB (its package file is missing)")
    base = posixpath.dirname(root)
    items = {}
    for tag in _tags(opf, "item"):
        iid, href = _attr(tag, "id"), _attr(tag, "href")
        path = _join(base, href or "")
        if iid and path:
            items[iid] = {"path": path, "type": (_attr(tag, "media-type") or "").lower(),
                          "props": (_attr(tag, "properties") or "").split()}
    spine = [items[_attr(t, "idref")] for t in _tags(opf, "itemref")
             if _attr(t, "idref") in items and (_attr(t, "linear") or "yes").lower() != "no"]
    toc = _attr(next(_tags(opf, "spine"), ""), "toc")
    nav = next((i for i in items.values() if "nav" in i["props"]), None)
    ncx = items.get(toc or "") or next((i for i in items.values() if i["type"] == "application/x-dtbncx+xml"), None)
    return spine, nav, ncx


def _locked(z: zipfile.ZipFile) -> bool:
    """Its pages are encrypted (DRM): META-INF/encryption.xml lists more than fonts."""
    try:
        text = _text(z, "META-INF/encryption.xml")
    except KeyError:
        return False
    return any(posixpath.splitext(unquote(_attr(t, "URI") or ""))[1].lower() not in FONTS
               for t in _tags(text, "CipherReference"))


def _page(z: zipfile.ZipFile, item: dict) -> tuple[list[str], int]:
    """A spine document's pictures (their files in the EPUB) and how much text it has besides."""
    if item["type"].startswith("image/") and item["type"] != "image/svg+xml":
        return [item["path"]], 0
    try:
        text = _text(z, item["path"])
    except KeyError:
        return [], 0
    base = posixpath.dirname(item["path"])
    pictures = []
    for tag in PICTURE_TAG.finditer(text):
        href = _attr(tag.group(), "src") or _attr(tag.group(), "xlink:href") or _attr(tag.group(), "href")
        path = _join(base, href or "")
        if path and posixpath.splitext(path)[1].lower() in PICTURES:
            pictures.append(path)
    body = re.search(r"<(?:[\w-]+:)?body\b[^>]*>(.*)</(?:[\w-]+:)?body\s*>", text, re.S | re.I)
    words = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", body.group(1) if body else "", flags=re.S | re.I)
    return pictures, len(_plain(words).replace(" ", ""))


def _contents(z: zipfile.ZipFile, nav: dict | None, ncx: dict | None) -> list[tuple[str, str]]:
    """The table of contents: (document, title), in its order (EPUB 3's, else EPUB 2's)."""
    for item, find in ((nav, "nav"), (ncx, "ncx")):
        if not item:
            continue
        try:
            text = _text(z, item["path"])
        except KeyError:
            continue
        base = posixpath.dirname(item["path"])
        if find == "nav":
            m = TOC_NAV.search(text)
            found = [(_join(base, href), _plain(label)) for _, href, label in LINK.findall(m.group(2))] if m else []
        else:
            found = [(_join(base, src), _plain(label)) for label, _, src in NCX_POINT.findall(text)]
        found = [(doc, title) for doc, title in found if doc and title]
        if found:
            return found
    return []


def _split(pages: list[str], marks: list[tuple[int, str]]) -> list[dict]:
    """The pages as chapters, by where the contents' entries start: [{"name", "pages"}]."""
    first = {}
    for start, title in marks:  # (the last entry on a page: a volume's first chapter, not the volume)
        if start < len(pages):
            first[start] = title
    starts = sorted(first)
    parts = [{"name": None, "pages": pages[:starts[0] if starts else len(pages)], "kind": "front"}]
    for k, start in enumerate(starts):
        title = first[start]
        kind = "out" if LEAVE_OUT.match(_key(title)) else "front" if FRONT.fullmatch(_key(title)) else "chapter"
        parts.append({"name": title, "pages": pages[start:starts[k + 1] if k + 1 < len(starts) else len(pages)],
                      "kind": kind})
    chapters = [x for x in parts if x["kind"] == "chapter"]
    if len(chapters) < 2 or sum(len(x["pages"]) for x in chapters) / len(chapters) < SHORTEST:
        return [{"name": None, "pages": [p for x in parts if x["kind"] != "out" for p in x["pages"]]}]
    out, before = [], []
    for x in parts:
        if x["kind"] == "chapter":
            out.append({"name": x["name"], "pages": before + x["pages"]})
            before = []
        elif x["kind"] == "front":
            if out:  # (pictures between chapters, an insert: with the chapter before)
                out[-1]["pages"] += x["pages"]
            else:
                before += x["pages"]
    return out


def chapters(path: Path) -> list[dict]:
    """The comic's chapters in the EPUB (importer.FILE_TYPES): [{"name", "pages"}], pages its pictures' files."""
    try:
        with zipfile.ZipFile(path) as z:
            if _locked(z):
                raise ValueError("it's locked with DRM (it opens only in the app of the store it came from); "
                                 "only DRM-free EPUBs can be imported")
            spine, nav, ncx = _book(z)
            have = set(z.namelist())
            pages, starts, pictured, texts = [], {}, 0, 0
            for item in spine:
                pictures, text = _page(z, item)
                starts.setdefault(item["path"], len(pages))
                pictures = [p for p in pictures if p in have and posixpath.splitext(p)[1].lower() in PICTURES]
                if pictures:
                    pictured += 1
                for p in pictures:
                    if not pages or pages[-1] != p:  # (one picture shown twice in a row: once)
                        pages.append(p)
                if not pictures and text >= TEXT_PAGE:
                    texts += 1
            if texts > pictured:
                raise ValueError("it's a book of text (a novel, say), not a comic's pages")
            marks = [(starts[doc], title) for doc, title in _contents(z, nav, ncx) if doc in starts]
    except zipfile.BadZipFile:
        raise ValueError("it isn't an EPUB (it isn't a zip file)")
    return _split(pages, marks) if pages else []


def pages(path: Path, wanted: list[str]):
    """The pictures, in order (importer.FILE_TYPES): (name, bytes)."""
    with zipfile.ZipFile(path) as z:
        for name in wanted:
            try:
                info = z.getinfo(name)
            except KeyError:
                continue
            if info.file_size <= MAX_PICTURE:
                yield name, z.read(info)


def setup(plugin):
    plugin.file_type(".epub", "EPUB", chapters, pages)
