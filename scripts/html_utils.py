from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin


class LinkExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {name.lower(): value for name, value in attrs if value is not None}
        href = attr_map.get("href")
        if href:
            self._current_href = urljoin(self.base_url, href)
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._current_href:
            return
        text = " ".join(" ".join(self._current_text).split())
        self.links.append({"url": self._current_href, "text": text})
        self._current_href = None
        self._current_text = []


def extract_links(html_text: str, base_url: str) -> list[dict[str, str]]:
    parser = LinkExtractor(base_url)
    parser.feed(html_text)
    return parser.links
