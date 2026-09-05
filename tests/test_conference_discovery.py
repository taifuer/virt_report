"""Offline regressions for editorial discovery, provenance and related links."""
import json
from pathlib import Path

import pytest
import requests

from virt_report import conference_watch, conferences, db, kvm_forum, server
from virt_report.config import Config
from virt_report.render import render


@pytest.mark.parametrize("title,abstract,expected", [
    ("STALEUS: Breaking AMD SEV-SNP via Memory Incoherence", "", True),
    ("Single-Stepping and Cache Attacks on Intel TDX", "", True),
    ("Side-Channel Attacks on Open vSwitch", "", True),
    ("Accelerating nested virtualisation on arm64", "", True),
    ("Virtual memory management in KVM", "", True),
    ("Virtual Memory for Large Databases", "", False),
    ("Opaque Paper Name", "We extend KVM to isolate virtual machines.", True),
    ("Opaque Paper Name", None, False),
    ("GPU training on a cloud cluster", "", False),
    ("Java Virtual Machine Garbage Collection", "Comparison with KVM", False),
    ("Virtual Reality Rendering", "Uses a hypervisor in the test setup", False),
])
def test_metadata_candidates_are_review_hints(title, abstract, expected):
    assert conferences.is_candidate_title(title, abstract) is expected


@pytest.mark.parametrize("parser,body,expected", [
    ("sosp", '<nav><b>Navigation</b></nav><ul class="paperlist">'
     '<li><b>A <i>KVM</i> Paper</b><br><em>Author, Institution</em></li></ul>'
     '<!-- <ul class="paperlist"><li><b>Old Paper</b></li></ul> -->',
     ["A KVM Paper"]),
    ("socc", '<h3>Welcome</h3><h2>Accepted papers</h2><h3></h3>'
     '<h3>CPU Virtualization</h3><h4>Authors</h4><h2>Sponsors</h2><h3>Logo</h3>',
     ["CPU Virtualization"]),
    ("usenix", '<h2>Lunch</h2><h2><a href="/conference/sec26/presentation/a">'
     'TDX &amp; KVM</a></h2><h2><a href="/conference/sec26/presentation/a">'
     'TDX &amp; KVM</a></h2><a href="/conference/sec26/presentation/b">Video</a>',
     ["TDX & KVM"]),
    ("pretalx", '<div class="title">Lunch</div><a href="/2026/talk/ABC/">'
     '<div class="pretalx-session"><div class="title">QEMU Migration</div>'
     '<div class="abstract">Long abstract</div></div></a>', ["QEMU Migration"]),
])
def test_official_lists_exclude_navigation_authors_and_comments(parser, body, expected):
    records = conference_watch.parse_title_list(body, {
        "parser": parser, "source_url": "https://example.com/program",
    })
    assert [item["title"] for item in records] == expected
    assert all(item["url"].startswith("https://example.com/") for item in records)


@pytest.mark.parametrize("body", ["", "<h1>Verify you are human</h1>",
                                   '<h2><a href="javascript:/presentation/">X</a></h2>'])
def test_invalid_official_page_is_not_an_empty_success(body):
    with pytest.raises(ValueError, match="未解析到"):
        conference_watch.parse_title_list(body, {
            "parser": "usenix", "source_url": "https://example.com/program",
        })


def test_official_checks_are_incremental_and_keep_last_success(tmp_path, monkeypatch):
    output = tmp_path / "checks.json"
    source = {"venue": "sosp", "year": 2026, "parser": "sosp",
              "source_url": "https://example.com/accepted"}
    content = {"edition_checks": [source], "papers": [
        {"venue": "sosp", "year": 2026, "title": "A KVM Paper"},
    ]}
    monkeypatch.setattr(conferences, "load_content", lambda: content)

    class Session:
        headers = {}
        trust_env = True
        titles = ["A KVM Paper", "New Intel TDX Paper"]
        failure = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, timeout):
            assert url == source["source_url"] and timeout == (10, 25)
            assert not self.trust_env
            if self.failure:
                raise requests.Timeout("timeout")
            response = requests.Response()
            response.status_code = 200
            response.headers["Content-Type"] = "text/html"
            response._content = ('<ul class="paperlist">' + ''.join(
                f'<li><b>{title}</b></li>' for title in self.titles
            ) + '</ul>').encode()
            return response

    monkeypatch.setattr(conference_watch.requests, "Session", Session)
    first = conference_watch.check_updates(output)["sources"][0]
    assert first["new_titles"] == 2
    assert [p["title"] for p in first["pending_candidates"]] == ["New Intel TDX Paper"]
    assert conference_watch.check_updates(output)["sources"][0]["new_titles"] == 0
    Session.titles = [*Session.titles, "Another SEV-SNP Paper"]
    assert conference_watch.check_updates(output)["sources"][0]["new_titles"] == 1
    success = json.loads(output.read_text())["sources"][0]
    Session.failure = True
    failed = conference_watch.check_updates(output)["sources"][0]
    assert failed["status"] == "error" and failed["new_titles"] is None
    saved = json.loads(output.read_text())["sources"][0]
    assert saved["titles"] == success["titles"]
    assert saved["last_success_at"] == success["last_success_at"]
    assert list(tmp_path.iterdir()) == [output]
    assert len(content["papers"]) == 1  # Checks never become published reviews.
    with pytest.raises(ValueError, match="未配置"):
        conference_watch.check_updates(output, venues=["unknown"])


def test_catalogue_uses_stored_abstract_for_opaque_titles(tmp_path):
    conn = db.connect(tmp_path / "catalogue.db")
    conn.execute("""INSERT INTO conference_papers
        (paper_id,venue,year,title,abstract,fetched_at)
        VALUES ('opaque','sosp',2026,'Opaque Name','We extend KVM.','2026-09-05')""")
    conn.commit()
    assert conferences.candidate_rows(conn)[0]["paper_id"] == "opaque"
    conn.close()


def test_reviewed_additions_keep_years_evidence_and_institutions():
    content = conferences.load_content()
    by_id = {item["id"]: item for item in content["papers"]}
    assert content["paper_count"] >= 90
    assert by_id["security25-tdxploit"]["year"] == 2025
    accepted = by_id["sosp26-cpu-virtualization"]
    assert accepted["publication_status"] == "accepted"
    assert accepted["evidence_level"] == "title" and accepted["architectures"] == []
    assert all(p["institutions"] for p in by_id.values())
    page = render.render_conference_papers_html(Config(), content)
    assert "data-conference-query" in page and "data-paper-topic" in page
    assert 'id="paper-security25-tdxploit"' in page
    assert "仅有公开标题，待摘要补充" in page
    assert 'href="topics/migration/index.html"' in page
    # The search index uses rendered text, not another copy of paper prose.
    assert 'data-search-text=' not in page


def test_related_research_is_small_and_links_to_real_versions():
    content = conferences.load_content()
    releases = json.loads((Path(conferences.CONTENT_PATH).parent / "versions.json").read_text())["releases"]
    release_ids = {f"release-{item['project']}-{item['version']}" for item in releases}
    for item in content["related_topics"]:
        assert 1 <= len(item["papers"]) <= 3
        for link in item["releases"]:
            assert link["href"].split("#")[1] in release_ids
    topic = {"key": "migration", "name": "热迁移", "items": [],
             "scope": "curated", "sort": "priority", "page": 1,
             "pages": 1, "per_page": 10}
    html = render.render_topic_detail_html(Config(), topic)
    assert 'id="related-research"' in html
    assert '../../conference-papers.html#paper-osdi26-m3u' in html
    assert "不表示论文已经进入上游或对应版本" in html


@pytest.mark.parametrize("path", ["/topics/migration", "/topics/migration/",
                                  "/topics/migration.html", "/topics/migration/index.html"])
def test_topic_links_work_with_dynamic_and_static_paths(path):
    assert server._TOPIC_ROUTE.fullmatch(path).group(1) == "migration"
    assert server._TOPIC_ROUTE.fullmatch("/topics/../index.html") is None


def test_forum_preview_keeps_historical_analysis_separate():
    editions, analysis = kvm_forum.load_content()
    preview = editions[-1]
    assert preview["year"] == 2026 and preview["preview"]
    assert len(preview["titles"]) == len(set(preview["titles"])) == 43
    assert len(preview["selected_talks"]) == 4
    assert all(item["year"] <= 2025 for item in analysis["years"])
    page = render.render_kvm_forum_html(Config(), editions, analysis)
    assert page.index('id="year-2026"') < page.index('id="year-2025"')
    assert "议程预览" in page and "2026-11-12" in page
    assert "43 个议题" in page
    assert '<h2>2026</h2>' in page
    assert page.count(f'href="{preview["url"]}"') == 1
    assert 'class="representative-papers"' not in page
    assert 'class="forum-preview-method"' not in page
    assert all(talk["url"] not in page for talk in preview["selected_talks"])
