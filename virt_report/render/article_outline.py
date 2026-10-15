"""Number trusted editorial headings and build their matching static outline."""
from __future__ import annotations

from collections import Counter
from html import escape
from html.parser import HTMLParser

from markupsafe import Markup


def chinese_number(value: int) -> str:
    """Format article section numbers, limited to the readable range 1–99."""
    if not 1 <= value <= 99:
        raise ValueError("An article must have between 1 and 99 sections")
    digits = "零一二三四五六七八九"
    tens, units = divmod(value, 10)
    return ((digits[tens] if tens > 1 else "") + "十" if tens else "") + (
        digits[units] if units else "")


class _Headings(HTMLParser):
    """Collect direct section headings, excluding nested interactive widgets."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.lines = [0]
        for line in html.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))
        self.stack: list[tuple[str, dict]] = []
        self.headings: list[dict] = []
        self.ids: set[str] = set()
        self.current: dict | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            if attributes["id"] in self.ids:
                raise ValueError(f'Duplicate article anchor: {attributes["id"]}')
            self.ids.add(attributes["id"])
        if tag in {"h2", "h3"} and self.stack:
            parent, parent_attrs = self.stack[-1]
            if parent == "section" and "mechanism-section" in parent_attrs.get("class", "").split():
                line, column = self.getpos()
                opening_end = self.lines[line - 1] + column + len(self.get_starttag_text())
                self.current = {"level": tag, "section": parent_attrs.get("id"),
                                "id": attributes.get("id"), "title": "",
                                "opening_end": opening_end}
                self.headings.append(self.current)
        if tag not in self.VOID:
            self.stack.append((tag, attributes))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.current is not None:
            self.current["title"] += data

    def handle_endtag(self, tag):
        if self.current and tag == self.current["level"]:
            self.current["title"] = " ".join(self.current["title"].split())
            self.current = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def prepare_outline(html: str, sections: list[dict]) -> tuple[Markup, list[dict]]:
    """Keep body, nested contents and fragment links identical without JavaScript."""
    parsed = _Headings(html)
    major = [heading for heading in parsed.headings if heading["level"] == "h2"]
    if [(h["section"], h["title"]) for h in major] != [
        (section["id"], section["title"]) for section in sections
    ]:
        raise ValueError("Editorial section order/titles disagree with the article metadata")
    subsection_counts = Counter(
        heading["section"] for heading in parsed.headings if heading["level"] == "h3"
    )
    outline, edits = [], []
    for heading in parsed.headings:
        if heading["level"] == "h2":
            label = chinese_number(len(outline) + 1) + "、"
            outline.append({"id": heading["section"], "title": heading["title"],
                            "number": label, "children": []})
        else:
            if not outline or outline[-1]["id"] != heading["section"]:
                raise ValueError("A subsection must follow its section heading")
            children = outline[-1]["children"]
            number = len(children) + 1
            label = f"{number}. " if subsection_counts[heading["section"]] > 1 else ""
            identifier = heading["id"] or f'{heading["section"]}-part-{number}'
            if not heading["id"]:
                if identifier in parsed.ids:
                    raise ValueError(f"Duplicate subsection anchor: {identifier}")
                parsed.ids.add(identifier)
                edits.append((heading["opening_end"] - 1, f' id="{escape(identifier, quote=True)}"'))
            children.append({"id": identifier, "title": heading["title"], "number": label})
        if label:
            edits.append((heading["opening_end"],
                          f'<span class="mechanism-heading-number">{label}</span>'))
    for position, text in sorted(edits, reverse=True):
        html = html[:position] + text + html[position:]
    return Markup(html), outline
