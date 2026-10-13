"""Offline editorial pages, source baselines and teaching-model regressions."""
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import pytest

from virt_report import mechanisms, server
from virt_report.config import Config, Render
from virt_report.render import render


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []
        self.scripts = []
        self.headings = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if tag == "a":
            self.links.append(attributes.get("href", ""))
        if tag == "script":
            self.scripts.append(attributes.get("src", ""))
        if tag == "h1":
            self.headings.append(tag)


def test_editorial_metadata_pins_each_repository_and_only_published_articles():
    articles = mechanisms.articles()
    assert len({article["slug"] for article in articles}) == len(articles)
    for article in articles:
        assert re.fullmatch(r"[a-z0-9-]+", article["slug"])
        assert mechanisms.get_topic(article["topic"]) is not None
        assert (render.TEMPLATES_DIR / article["template"]).is_file()
        assert "QEMU" in {item["project"] for item in article["baselines"]}
        assert article["script"] in render.MECHANISM_ASSET_NAMES
        assert article["stylesheet"] in render.MECHANISM_ASSET_NAMES
        for baseline in article["baselines"]:
            assert re.fullmatch(r"[0-9a-f]{40}", baseline["commit"])
            assert baseline["url"].endswith(baseline["commit"])
    assert mechanisms.get_article("../base") is None
    assert mechanisms.get_topic("../base") is None


def test_editorial_topics_group_published_articles_and_omit_empty_sections(monkeypatch):
    metadata = {
        "topics": [
            {"slug": "migration", "title": "热迁移"},
            {"slug": "empty", "title": "尚无文章"},
        ],
        "articles": [
            {"slug": "first", "topic": "migration"},
            {"slug": "second", "topic": "migration"},
        ],
    }
    monkeypatch.setattr(mechanisms, "_content", lambda: metadata)
    mechanisms.topics.cache_clear()
    try:
        grouped = mechanisms.topics()
        assert len(grouped) == 1
        assert grouped[0]["slug"] == "migration"
        assert [article["slug"] for article in grouped[0]["articles"]] == ["first", "second"]
        assert mechanisms.get_topic("empty") is None
        assert "articles" not in metadata["topics"][0]
    finally:
        mechanisms.topics.cache_clear()


@pytest.mark.parametrize("slug", ["qemu-memory-migration", "qemu-migration-stream"])
def test_article_has_matching_toc_source_evidence_and_no_inline_scripts(slug):
    config = Config()
    article = mechanisms.get_article(slug)
    page = render.render_mechanisms_html(config, article["slug"])
    parsed = PageParser()
    parsed.feed(page)
    assert len(parsed.ids) == len(set(parsed.ids))
    assert len(parsed.headings) == 1
    assert f'{article["title"]} - virt-report' in page
    for section in article["sections"]:
        assert section["id"] in parsed.ids
        assert f'#{section["id"]}' in parsed.links
        assert f'<h2>{section["title"]}</h2>' in page
    assert all(src.startswith("../assets/") for src in parsed.scripts)
    assert f'../assets/{article["script"]}?v=' in page
    assert f'../assets/{article["stylesheet"]}?v=' in page
    assert "../assets/mechanisms.css?v=" in page
    assert "../assets/mechanism-viewer.css?v=" in page
    assert "../assets/mechanism-viewer.js?v=" in page
    assert page.index("assets/mechanism-viewer.js?v=") < page.index(
        f'assets/{article["script"]}?v=')
    assert f'../mechanisms.html#{article["topic"]}' in parsed.links
    assert "热迁移原理</a>" in page
    assert "热迁移动态</a>" in page
    assert "热迁移专题</a>" not in page
    assert "../topics/migration/index.html" in parsed.links
    assert page.index('id="preface"') < page.index('class="mechanism-baselines"')
    assert "该限定用于读清一条完整路径" not in page
    assert "热迁移转移的是一台正在运行的虚拟机" not in page
    assert "#L" in page and "/blob/698104725efad4b29079d857dfdebbd804e34c99/" in page
    assert "/root/codes/" not in page
    assert all(link[1:] in parsed.ids for link in parsed.links if link.startswith("#"))


def test_articles_cover_distinct_mechanisms_and_link_to_each_other():
    memory = render.render_mechanisms_html(Config(), "qemu-memory-migration")
    stream = render.render_mechanisms_html(Config(), "qemu-migration-stream")
    for page, target in ((memory, "qemu-migration-stream.html"),
                         (stream, "qemu-memory-migration.html")):
        parsed = PageParser()
        parsed.feed(page)
        assert any(link.split("/")[-1] == target for link in parsed.links)
    assert 'data-migration-interactive hidden' in memory
    assert 'class="mechanism-diagram"' in memory
    assert "Mermaid 源码" in memory
    assert "/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/" in memory
    for marker in ("downtime-limit", "expected-downtime", "auto-converge", "dirty-limit", "MultiFD"):
        assert marker in memory
    for marker in ("QEMU_VM_FILE_MAGIC", "QEMU_VM_SECTION_", "SECTION_START", "SECTION_END",
                   "QEMU_VM_SECTION_FOOTER", "RAM_SAVE_FLAG_EOS", "VMDESCRIPTION",
                   "SWITCHOVER_START"):
        assert marker in stream
    assert "../assets/migration-wire-layout.svg?v=" in stream
    assert "运行中每个传输步写入的页面数" not in memory


@pytest.mark.parametrize("slug", ["qemu-memory-migration", "qemu-migration-stream"])
def test_article_related_content_keeps_only_two_migration_links(slug):
    page = render.render_mechanisms_html(Config(), slug)
    matched = re.search(
        r'<nav\b[^>]*\bclass="mechanism-related"[^>]*>(.*?)</nav>',
        page, re.DOTALL,
    )
    assert matched is not None
    navigation = matched.group(0)
    content = matched.group(1)
    assert 'aria-label="相关内容"' in navigation
    assert re.match(r'\s*<span\b[^>]*>相关内容</span>\s*<a\b', content)
    parsed = PageParser()
    parsed.feed(navigation)
    assert parsed.links == [
        "../mechanisms.html#migration",
        "../topics/migration/index.html",
    ]
    assert re.findall(r'<a\b[^>]*>([^<]+)</a>', navigation) == ["热迁移原理", "热迁移动态"]
    for removed in ("相关版本", "会议研究", "全部原理文章"):
        assert removed not in navigation


def test_catalogue_has_one_title_link_per_article_without_redundant_cta():
    page = render.render_mechanisms_html(Config())
    assert "阅读正文与图解" not in page
    assert "源码基线" not in page
    assert "QEMU 10.2.0" not in page
    for article in mechanisms.articles():
        assert page.count(f'href="mechanisms/{article["slug"]}.html"') == 1
        assert f'<h3><a href="mechanisms/{article["slug"]}.html">' in page
    assert 'id="migration" aria-labelledby="mechanism-topic-migration"' in page
    assert '<h2 id="mechanism-topic-migration">热迁移</h2>' in page
    assert page.count('class="mechanism-topic"') == 1


def test_catalogue_about_and_export_work_without_database_and_preserve_reports(tmp_path):
    config = Config(render=Render(output_dir=tmp_path))
    historical = tmp_path / "daily" / "2026-10-12.html"
    historical.parent.mkdir()
    historical.write_text("historical report", encoding="utf-8")
    exported = render.render_mechanisms(config)
    assert {path.relative_to(tmp_path).as_posix() for path in exported} == {
        "mechanisms.html", "mechanisms/qemu-memory-migration.html",
        "mechanisms/qemu-migration-stream.html",
    }
    assert historical.read_text() == "historical report"
    for name in render.MECHANISM_ASSET_NAMES:
        assert (tmp_path / "assets" / name).read_bytes() == (
            render.ASSETS_DIR / name).read_bytes()
    assert 'href="mechanisms/qemu-memory-migration.html"' in (
        tmp_path / "mechanisms.html").read_text()
    assert 'href="mechanisms.html">查看原理' in render.render_about_html(config)


@pytest.mark.parametrize("path,status,marker", [
    ("/mechanisms.html", 200, "Virtualization Internals"),
    ("/mechanisms/qemu-memory-migration.html", 200, "QEMU 预拷贝迁移的内存迭代与停机控制"),
    ("/mechanisms/qemu-migration-stream.html", 200, "QEMU 迁移流格式与状态恢复"),
    ("/mechanisms/unknown.html", 404, "Not found"),
    ("/mechanisms/../../base.html", 404, "404"),
    ("/assets/mechanisms.js", 200, "createState"),
    ("/assets/mechanisms.css", 200, "prefers-reduced-motion"),
    ("/assets/precopy-lab.css", 200, "precopy-"),
    ("/assets/migration-stream.css", 200, "stream-"),
    ("/assets/migration-stream.js", 200, "function"),
    ("/assets/mechanism-viewer.css", 200, "mechanism-"),
    ("/assets/mechanism-viewer.js", 200, "function"),
    ("/assets/migration-architecture.svg", 200, "<svg"),
    ("/assets/migration-precopy-sequence.svg", 200, "<svg"),
    ("/assets/migration-wire-layout.svg", 200, "<svg"),
])
def test_editorial_routes_do_not_open_database(monkeypatch, path, status, marker):
    def forbidden(*args, **kwargs):
        pytest.fail("Editorial pages must not open the database")

    monkeypatch.setattr(server.db, "connect", forbidden)
    handler_type = server.make_handler(Config())
    handler = object.__new__(handler_type)
    handler.path = path
    responses = []
    handler._send = lambda code, body, *_args, **_kwargs: responses.append((code, body))
    handler._dispatch(head_only=False)
    code, body = responses[0]
    assert code == status
    assert marker in (body.decode() if isinstance(body, bytes) else body)


def test_editorial_render_is_cached_and_rejects_unregistered_templates():
    render._render_mechanism_page.cache_clear()
    first = render.render_mechanisms_html(Config(), "qemu-memory-migration")
    second = render.render_mechanisms_html(Config(), "qemu-memory-migration")
    assert first == second
    assert render._render_mechanism_page.cache_info().hits == 1
    assert render.render_mechanisms_html(Config(), "base.html") is None


def test_diagrams_are_self_contained_svg_with_accessible_descriptions():
    for name in (*render.MECHANISM_DIAGRAM_NAMES, *render.MECHANISM_LAYOUT_NAMES):
        svg = (render.ASSETS_DIR / f"{name}.svg").read_text(encoding="utf-8")
        root = ElementTree.fromstring(svg)
        assert root.tag == "{http://www.w3.org/2000/svg}svg"
        assert root.get("viewBox")
        assert list(root.iter("{http://www.w3.org/2000/svg}title"))
        assert list(root.iter("{http://www.w3.org/2000/svg}desc"))
        for node in root.iter():
            assert not node.tag.endswith(("script", "foreignObject"))
            assert not any(key.lower().startswith("on") for key in node.attrib)
            assert not any(value.startswith(("http:", "https:", "//"))
                           for key, value in node.attrib.items() if key.endswith("href"))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for browser-model tests")
@pytest.mark.parametrize("filename", ["test_migration_model.cjs", "test_migration_stream.cjs"])
def test_migration_model_state_invariants(filename):
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name(filename))],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
