"""Turn an HTML page into title, headings, text blocks and same-page links (standard library)."""

from __future__ import annotations

import re
from html.parser import HTMLParser

SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "template", "nav", "header", "footer", "form", "select", "button"}
BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "dt", "dd", "figcaption", "blockquote", "summary", "caption"}
HEADINGS = {"h1", "h2", "h3", "h4"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# Page builders put the site header and footer in ordinary <div>s.
CHROME_ATTR = re.compile(r"(?i)(?:^|[\s_-])(?:site-header|site-footer|elementor-location-header|elementor-location-footer|navbar|breadcrumbs?|cookie|skip-link|screen-reader)(?:$|[\s_-])")


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.headings: list[tuple[str, str]] = []
        self.blocks: list[str] = []
        self.links: list[str] = []
        self.images = 0
        self._skip_tag: str | None = None
        self._skip_depth = 0
        self._in_title = False
        self._current: list[str] | None = None
        self._current_tag = ""
        self._loose: list[str] = []

    def _chrome(self, tag: str, attrs: dict[str, str]) -> bool:
        if tag in SKIP_TAGS:
            return True
        if attrs.get("data-elementor-type") in {"header", "footer", "popup"}:
            return True
        if attrs.get("role") in {"navigation", "banner", "contentinfo"}:
            return True
        return bool(CHROME_ATTR.search(f"{attrs.get('class', '')} {attrs.get('id', '')}"))

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k: (v or "") for k, v in attrs_list}
        # Links count even inside the header and menu: the main navigation lives there.
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "img":
            self.images += 1
        if tag in VOID:
            if tag == "br" and self._current is not None:
                self._current.append(" ")
            return
        if self._chrome(tag, attrs):
            self._skip_tag, self._skip_depth = tag, 1
            return
        if tag in BLOCK_TAGS:
            self._flush_loose()
            self._close_block()
            self._current, self._current_tag = [], tag

    def handle_endtag(self, tag: str) -> None:
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth <= 0:
                    self._skip_tag = None
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in BLOCK_TAGS and self._current is not None and tag == self._current_tag:
            self._close_block()
        elif tag in {"div", "section", "article", "span"} and self._current is None:
            self._flush_loose()

    def handle_data(self, data: str) -> None:
        if self._skip_tag:
            return
        if self._in_title:
            self.title += data
            return
        if self._current is not None:
            self._current.append(data)
        elif data.strip():
            self._loose.append(data)

    def _close_block(self) -> None:
        if self._current is None:
            return
        text = re.sub(r"\s+", " ", "".join(self._current)).strip()
        if text:
            self.blocks.append(text)
            if self._current_tag in HEADINGS:
                self.headings.append((self._current_tag, text))
        self._current = None

    def _flush_loose(self) -> None:
        text = re.sub(r"\s+", " ", " ".join(self._loose)).strip()
        if len(text) >= 20:
            self.blocks.append(text)
        self._loose = []

    def close(self) -> None:
        super().close()
        self._close_block()
        self._flush_loose()


def parse_html(html: str) -> dict:
    parser = PageText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # broken markup: keep what was read
        pass
    h1 = next((text for tag, text in parser.headings if tag == "h1"), "")
    return {
        "title": re.sub(r"\s+", " ", parser.title).strip(),
        "h1": h1,
        "headings": [text for _, text in parser.headings],
        "blocks": parser.blocks,
        "links": parser.links,
        "images": parser.images,
    }
