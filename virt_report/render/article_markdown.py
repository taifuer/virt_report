"""Render editorial Markdown with stable sections and explicit local components.

Prose is never evaluated as Jinja or raw HTML. Only registered diagram/component
fences can insert the HTML produced by the existing, trusted templates.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from html import escape

from markdown_it import MarkdownIt
from markdown_it.token import Token


_SECTION = re.compile(r"^(.+?)\s+\{#([a-z][a-z0-9-]*)\}$")


def _html_token(content: str) -> Token:
    token = Token("html_block", "", 0)
    # Jinja macros return Markup. Its reflected concatenation would escape the
    # already-rendered Markdown when markdown-it joins tokens; normalize here.
    token.content = str(content) + "\n"
    return token


def render_article_markdown(
    source: str,
    *,
    baselines: str,
    diagrams: Mapping[str, Callable[[str, str], str]],
    components: Mapping[str, Callable[[], str]],
) -> str:
    """Render authored content; metadata/outline validation follows at publication.

    H2s require ``{#stable-id}``. Fenced ``diagram NAME`` blocks contain a
    title and description; ``baselines`` and ``component NAME`` have no body.
    Ordinary code fences remain literal, including Jinja-looking examples.
    """
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    env: dict = {}
    tokens = parser.parse(source, env)
    output: list[Token] = []
    section = None
    baselines_seen = False
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h1":
            raise ValueError("Article title belongs in metadata, not Markdown H1")
        if token.type == "heading_open" and token.tag == "h2":
            if token.level != 0:
                raise ValueError("Article sections must be top-level H2 headings")
            inline = tokens[index + 1]
            match = _SECTION.fullmatch(inline.content)
            if not match:
                raise ValueError("Markdown H2 requires a stable {#section-id}")
            title, identifier = match.groups()
            if section is not None:
                output.append(_html_token("</section>"))
            elif identifier != "preface":
                raise ValueError("The first article section must be preface")
            section = identifier
            output.append(_html_token(
                f'<section id="{escape(identifier, quote=True)}" class="mechanism-section">'))
            inline.content = title
            inline.children = parser.parseInline(title, env)[0].children
        elif token.type == "heading_open" and token.tag == "h3" and token.level == 0:
            inline = tokens[index + 1]
            match = _SECTION.fullmatch(inline.content)
            if match:
                title, identifier = match.groups()
                token.attrSet("id", identifier)
                inline.content = title
                inline.children = parser.parseInline(title, env)[0].children
        if section is None:
            raise ValueError("Article prose must start with its preface H2")
        if token.type == "fence":
            command = token.info.strip().split()
            kind = command[0] if command else ""
            if kind in {"baselines", "diagram", "component"}:
                if token.level != 0:
                    raise ValueError("Article components must be standalone blocks")
                if kind == "baselines":
                    if len(command) != 1 or token.content.strip():
                        raise ValueError("The baselines fence takes no arguments or body")
                    if baselines_seen or section != "preface":
                        raise ValueError("Baselines must appear once, inside the preface")
                    baselines_seen = True
                    html = baselines
                elif kind == "diagram":
                    lines = token.content.strip().splitlines()
                    if len(command) != 2 or command[1] not in diagrams or len(lines) < 2:
                        raise ValueError("A diagram needs a registered name, title and description")
                    html = diagrams[command[1]](lines[0], " ".join(lines[1:]))
                else:
                    if len(command) != 2 or command[1] not in components or token.content.strip():
                        raise ValueError("A component needs an allowlisted name and no body")
                    html = components[command[1]]()
                output.append(_html_token(html))
                continue
        # Match the semantics/accessibility of hand-authored article tables.
        if token.type == "th_open":
            token.attrSet("scope", "col")
        output.append(token)
    if not section or not baselines_seen:
        raise ValueError("An article requires a preface and source baselines")
    output.append(_html_token("</section>"))
    return parser.renderer.render(output, parser.options, env)
