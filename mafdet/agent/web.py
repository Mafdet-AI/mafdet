"""The web, when the user turns it on (--web): a page as text, and a search.

Off by default: the point of a local agent is that nothing leaves the machine, and a URL or a
query is something that leaves. With --web the two tools run without asking, like a read.

A local model knows less than a large one, and what it lacks is often one page away: the
documentation of a tool, a file format, an API. `web_fetch` returns the page as text (the loop cuts
it and keeps the whole of it on disk, like any output); `web_search` returns titles, addresses and
snippets from DuckDuckGo's HTML page, or from a SearXNG of the user's (`search_url` in agent.json).
No key, no dependency; the HTML page is not an API and can change.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from .tools import Tool

AGENT = "Mozilla/5.0 (compatible; mafdet-agent)"
MAX_BYTES = 3_000_000
TIMEOUT = 25
DUCKDUCKGO = "https://html.duckduckgo.com/html/?q={query}"
SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe"}
BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "section", "article", "table",
          "ul", "ol", "blockquote", "header", "footer", "dt", "dd"}


class _Text(HTMLParser):
    """A page's text: no scripts or styles, a line per block, links kept as text (address)."""

    def __init__(self, base: str = ""):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.out: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False
        self._href = ""

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += tag != "head"
            return
        if tag == "title":
            self._in_title = True
        if tag in BLOCKS:
            self.out.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.out.append("#" * int(tag[1]) + " ")
        if tag == "li":
            self.out.append("- ")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self._href = urllib.parse.urljoin(self.base, href) if href and not href.startswith(("#", "javascript:")) else ""

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip = max(0, self.skip - (tag != "head"))
            return
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._href:
            self.out.append(f" ({self._href})")
            self._href = ""
        if tag in BLOCKS:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)

    def text(self) -> str:
        text = "".join(self.out)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" ?\n ?", "\n", text)
        return re.sub(r"\n{2,}", "\n", text).strip()            # no blank lines: they cost tokens


def html_to_text(html: str, base: str = "") -> tuple[str, str]:
    """(title, text) of an HTML page."""
    parser = _Text(base)
    try:
        parser.feed(html)
        parser.close()
    except Exception:                       # a broken page: what was read so far
        pass
    return " ".join(parser.title.split()), parser.text()


def get(url: str) -> tuple[str, str, str]:
    """(final address, content type, text) of a GET; raises ValueError with what went wrong."""
    if not re.match(r"https?://", url, re.I):
        raise ValueError("only http and https addresses")
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "text/html,text/plain,*/*;q=0.5",
                                               "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            kind = r.headers.get_content_type()
            charset = r.headers.get_content_charset() or "utf-8"
            data = r.read(MAX_BYTES + 1)
            final = r.geturl()
    except urllib.error.HTTPError as e:
        raise ValueError(f"the server answered {e.code} {e.reason}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ValueError(f"cannot reach it: {getattr(e, 'reason', e)}") from None
    if not (kind.startswith("text/") or kind in ("application/json", "application/xml", "application/xhtml+xml",
                                                 "application/javascript")):
        raise ValueError(f"not text ({kind}, {len(data)} bytes): download it with the shell if it is needed")
    try:
        text = data[:MAX_BYTES].decode(charset, "replace")
    except LookupError:
        text = data[:MAX_BYTES].decode("utf-8", "replace")
    return final, kind, text


def fetch(url: str) -> str:
    try:
        final, kind, text = get(url)
    except ValueError as e:
        return f"error: {e}"
    if kind in ("text/html", "application/xhtml+xml"):
        title, text = html_to_text(text, final)
        return (f"{title}\n{final}\n\n" if title else f"{final}\n\n") + (text or "[no text in the page]")
    return f"{final}\n\n{text}"


def _duckduckgo(html: str) -> list[dict]:
    """The results of DuckDuckGo's HTML page: links of class result__a, snippets of class result__snippet."""
    out = []
    links = re.findall(r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
    snippets = re.findall(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div|td)>', html, re.S)
    for i, (href, title) in enumerate(links):
        href = href.replace("&amp;", "&")
        target = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg")
        url = target[0] if target else ("https:" + href if href.startswith("//") else href)
        if "duckduckgo.com/y.js" in url:            # an ad
            continue
        out.append({"title": html_to_text(title)[1], "url": url,
                    "snippet": html_to_text(snippets[i])[1] if i < len(snippets) else ""})
    return out


def search(query: str, search_url: str = "", limit: int = 8) -> str:
    query = " ".join(str(query).split())
    if not query:
        return "error: an empty query"
    quoted = urllib.parse.quote_plus(query)
    try:
        if search_url:                              # a SearXNG: .../search?q={query}&format=json
            _, _, text = get(search_url.replace("{query}", quoted))
            results = [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
                       for r in json.loads(text).get("results", [])]
        else:
            _, _, text = get(DUCKDUCKGO.format(query=quoted))
            results = _duckduckgo(text)
    except ValueError as e:
        return f"error: {e}"
    if not results:
        return "no result (or the search page changed its shape): try other words, or web_fetch a known address"
    return "\n\n".join(f"{r['title']}\n{r['url']}\n{r['snippet']}".strip() for r in results[:limit])


def web_tools(search_url: str = "") -> list[Tool]:
    return [
        Tool("web_search", "Search the web: titles, addresses and snippets.",
             {"type": "object", "required": ["query"],
              "properties": {"query": {"type": "string", "description": "the words to search for"}}},
             lambda a: search(a["query"], search_url)),
        Tool("web_fetch", "Read a web page as text.",
             {"type": "object", "required": ["url"],
              "properties": {"url": {"type": "string", "description": "an http or https address"}}},
             lambda a: fetch(a["url"])),
    ]
