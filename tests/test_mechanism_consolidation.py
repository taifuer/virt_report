"""Merged articles retain old bookmarks without duplicating the catalogue."""
import io
import re
import shutil
import subprocess
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

import pytest

from virt_report import mechanisms, server
from virt_report.config import Config, Render
from virt_report.render import render


MERGES = {
    "vm-startup": "libvirt-domain-start",
    "live-upgrade": "qemu-cpr-transfer",
    "kvm-internals": "kvm-vcpu-run",
}
OLD_ANCHORS = {
    "vm-startup": {"preface", "state", "process", "example", "evolution",
                   "evaluation", "reading", "sources"},
    "live-upgrade": {"preface", "state", "process", "example", "evolution",
                     "evaluation", "reading", "sources"},
    "kvm-internals": {"preface", "objects", "execution", "resources",
                      "coordination", "evolution", "summary", "reading", "sources"},
}


class LinksAndIds(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = []
        self.links = []
        self.scripts = []
        self.canonical = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "a":
            self.links.append(attrs.get("href", ""))
        if tag == "script":
            self.scripts.append(attrs)
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical.append(attrs.get("href"))


def _forbid_database(*_args, **_kwargs):
    pytest.fail("Mechanism redirects and article exports must not open the database")


@pytest.fixture
def no_database(monkeypatch):
    monkeypatch.setattr(server.db, "connect", _forbid_database)
    monkeypatch.setattr(server.db, "connect_readonly", _forbid_database)


def _request(path, method):
    handler = object.__new__(server.make_handler(Config()))
    handler.path = path
    handler.headers = {}
    handler.wfile = io.BytesIO()
    statuses = []
    headers = {}
    handler.send_response = statuses.append
    handler.send_header = lambda name, value: headers.__setitem__(name, value)
    handler.end_headers = lambda: None
    getattr(handler, f"do_{method}")()
    assert len(statuses) == 1
    return statuses[0], headers, handler.wfile.getvalue()


def test_consolidated_catalogue_has_unique_canonical_articles():
    slugs = [article["slug"] for article in mechanisms.articles()]
    assert len(slugs) == len(set(slugs)) == 60
    assert set(slugs).isdisjoint(MERGES)
    assert {entry["slug"]: entry["target"] for entry in mechanisms.redirects()} == MERGES
    catalogue = LinksAndIds(render.render_mechanisms_html(Config()))
    article_links = [link.removeprefix("mechanisms/").removesuffix(".html")
                     for link in catalogue.links
                     if re.fullmatch(r"mechanisms/[a-z0-9-]+\.html", link)]
    assert Counter(article_links) == Counter(slugs)
    assert len(catalogue.ids) == len(set(catalogue.ids))


def test_export_includes_legacy_redirects_without_touching_reports(tmp_path, no_database):
    output = tmp_path / "site"
    reports = {}
    for relative in ("daily/2026-10-18.html", "weekly/2026-W42.html",
                     "monthly/2026-09.html", "index.html"):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"historical snapshot: {relative}".encode())
        reports[path] = (path.read_bytes(), path.stat().st_mtime_ns)
    config = Config(render=Render(output_dir=output))
    paths = render.render_mechanisms(config)
    assert len(paths) == len(set(paths)) == 1 + len(mechanisms.articles()) + len(MERGES)
    for old, target in MERGES.items():
        path = output / "mechanisms" / f"{old}.html"
        assert path in paths
        parsed = LinksAndIds(path.read_text(encoding="utf-8"))
        assert parsed.canonical == [f"{target}.html"]
        assert f"{target}.html" in parsed.links
        assert parsed.scripts == [{
            "src": "../assets/mechanism-redirect.js",
            "data-target": f"{target}.html", "defer": None,
        }]
        assert (output / "mechanisms" / f"{target}.html").is_file()
    assert (output / "assets" / "mechanism-redirect.js").is_file()
    for path, (body, modified) in reports.items():
        assert path.read_bytes() == body
        assert path.stat().st_mtime_ns == modified


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("old,target", MERGES.items())
def test_http_legacy_routes_use_308_without_database(old, target, method, no_database):
    status, headers, body = _request(f"/mechanisms/{old}.html", method)
    assert status == 308
    assert headers["Location"] == f"/mechanisms/{target}.html"
    assert headers["Content-Length"] == "0"
    assert body == b""


@pytest.mark.parametrize("slug", ["unknown-article", "base", "qemu-cpr-transfer-old"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_unregistered_articles_are_not_redirected(slug, method, no_database):
    assert mechanisms.get_redirect(slug) is None
    assert render.render_mechanisms_html(Config(), slug) is None
    status, headers, body = _request(f"/mechanisms/{slug}.html", method)
    assert status == 404
    assert "Location" not in headers
    assert body == (b"Not found" if method == "GET" else b"")


@pytest.mark.parametrize("old,target", MERGES.items())
def test_legacy_bookmark_anchors_survive_near_relevant_sections(old, target):
    html = render.render_mechanisms_html(Config(), target)
    parsed = LinksAndIds(html)
    assert len(parsed.ids) == len(set(parsed.ids))
    assert OLD_ANCHORS[old] <= set(parsed.ids)
    redirect = mechanisms.get_redirect(old)
    sections = {section["id"] for section in mechanisms.get_article(target)["sections"]}
    for legacy, destination in redirect["anchors"].items():
        assert destination in sections
        alias = re.search(r'<span\b(?=[^>]*\bid="' + re.escape(legacy)
                          + r'")(?=[^>]*\baria-hidden="true")[^>]*></span>', html)
        assert alias is not None, (old, legacy, destination)
        following = html[alias.end():]
        # Several old anchors may point to the same section, but no unrelated
        # content should separate the bookmark from its destination heading.
        following = re.sub(r'^(?:\s*<span\b(?=[^>]*\baria-hidden="true")'
                           r'[^>]*></span>)*',
                           '', following).lstrip()
        assert following.startswith(f'<section id="{destination}"'), (
            old, legacy, destination,
        )


def test_all_internal_article_links_and_fragments_resolve():
    pages = {"/mechanisms.html": render.render_mechanisms_html(Config())}
    pages.update({f"/mechanisms/{article['slug']}.html":
                  render.render_mechanisms_html(Config(), article["slug"])
                  for article in mechanisms.articles()})
    pages.update({f"/mechanisms/{old}.html": render.render_mechanisms_html(Config(), old)
                  for old in MERGES})
    parsed = {path: LinksAndIds(html) for path, html in pages.items()}
    for source, document in parsed.items():
        for link in document.links:
            resolved = urlsplit(urljoin(f"https://example.test{source}", link))
            if resolved.netloc != "example.test":
                continue
            if not (resolved.path.startswith("/mechanisms/")
                    or resolved.path == "/mechanisms.html"):
                continue
            assert resolved.path in parsed, (source, link, resolved.path)
            target_path = resolved.path
            slug = target_path.rsplit("/", 1)[-1].removesuffix(".html")
            if slug in MERGES:
                target_path = f"/mechanisms/{MERGES[slug]}.html"
            if resolved.fragment:
                assert unquote(resolved.fragment) in parsed[target_path].ids, (
                    source, link, target_path,
                )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional")
def test_static_redirect_preserves_query_and_bookmark_and_rejects_external_target():
    script = render.ASSETS_DIR / "mechanism-redirect.js"
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
for (const target of ['libvirt-domain-start.html', 'qemu-cpr-transfer.html',
                       'kvm-vcpu-run.html', 'https://evil.test/', '../base.html']) {
  const calls = [];
  vm.runInNewContext(source, {
    document: {querySelector: () => ({dataset: {target}})},
    window: {location: {search: '?from=bookmark', hash: '#process',
                       replace: value => calls.push(value)}}
  });
  const expected = /^[a-z0-9-]+\.html$/.test(target)
    ? [target + '?from=bookmark#process'] : [];
  assert.deepEqual(calls, expected);
}
"""
    result = subprocess.run(["node", "-e", program, str(script)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
