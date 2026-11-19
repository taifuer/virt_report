"""Keep publication status independent from the available review evidence."""

from copy import deepcopy
from html import escape

from virt_report import conferences, editorial_search, kvm_forum
from virt_report.config import Config
from virt_report.render import render as html_render


def test_conference_directory_omits_maintenance_notes_without_changing_records():
    content = conferences.load_content()
    before = deepcopy(content)
    page = html_render.render_conferences_html(Config(), content)
    assert content["edition_checks"]
    assert "venue-check" not in page
    assert "核对于" not in page
    assert "本轮未复核" not in page
    assert "已收录至" not in page
    assert "核对进度" not in page
    for venue in content["paper_venues"]:
        assert f'<h3>{escape(venue["name"])}</h3>' in page
        assert f'<div class="venue-description"><p>{escape(venue["focus"])}</p></div>' in page
        assert (f'href="conference-papers.html?scope=all&amp;venue={venue["key"]}">'
                f'{venue["count_label"]}</a>') in page
        assert (f'href="{escape(venue["url"])}" target="_blank" '
                'rel="noopener noreferrer">官方网站</a>') in page
    timeline = html_render.render_academic_conferences_html(Config(), content)
    assert '<nav class="kicker"' not in timeline
    assert 'href="conferences.html#conference-sources"' not in timeline
    assert 'venue=systor"' not in page
    assert "核对进度" not in timeline
    assert content == before


def test_paper_archive_keeps_topic_tags_without_community_topic_row():
    content = conferences.load_content()
    page = html_render.render_conference_papers_html(Config(), content)
    assert "paper-community-links" not in page
    assert "社区专题" not in page
    paper = page.split('id="paper-osdi26-m3u"', 1)[1].split("</article>", 1)[0]
    assert 'data-paper-topic="在线迁移"' in paper
    assert 'data-paper-topic="后拷贝"' in paper
    assert "editor-comment" in paper


def test_kvm_forum_post_event_agenda_keeps_withdrawals_and_title_updates():
    editions, analysis = kvm_forum.load_content()
    edition = next(item for item in editions if item["year"] == 2026)
    talks = {item["url"]: item for item in edition["talks"]}

    assert "preview" not in edition
    assert edition["starts_on"] == "2026-11-12"
    assert edition["ends_on"] == "2026-11-13"
    assert edition["checked_at"] == "2026-11-15"
    assert len(talks) == len(edition["talks"]) == 43
    assert "https://pretalx.com/kvm-forum-2026/talk/KP3ZN7/" not in talks
    assert talks["https://pretalx.com/kvm-forum-2026/talk/BY8NR3/"]["title"] == (
        "Artificial Intuition: Lessons Learned From Vibe-Auditing KVM"
    )
    assert talks["https://pretalx.com/kvm-forum-2026/talk/ZB3GWA/"]["title"] == (
        "COCONUT-SVSM, KVM Planes, and Secure Interrupt Injection - Upstream Alignment"
    )
    assert {talk["url"] for talk in edition["review"]["talks"]} <= talks.keys()

    page = html_render.render_kvm_forum_html(Config(), editions, analysis)
    assert "43 个议题" in page
    assert "原始讲稿" in page
    assert "议程预览" not in page and "核对于" not in page
    assert "依据会前议程归纳" not in page
    assert "Scheduling KVM Sandboxes" not in page


def test_sosp_editions_track_parseable_lists_and_restored_paper_has_evidence():
    content = conferences.load_content()
    paper = next(p for p in content["papers"] if p["id"] == "sosp26-cpu-virtualization")
    audit = next(p for p in content["screening"]["papers"]
                 if p["id"] == paper["id"])
    assert (audit["origin"], audit["decision"], audit["review_basis"]) == (
        "existing", "keep", "primary_abstract")
    assert paper["doi"] == "10.1145/3830418.3843895" and paper["published_on"] == "2026-09-28"
    assert paper["evidence_source_url"] == audit["source_url"]
    assert paper["evidence_level"] == "abstract" and not paper["curated"]
    assert "DDVM" in paper["introduction"] and "超配" in paper["commentary"]
    checks = {c["year"]: c for c in content["edition_checks"] if c["venue"] == "sosp"}
    for year in (2025, 2026):
        assert checks[year]["parser"] == "sosp"
        assert checks[year]["source_url"].endswith(f"/sosp/{year}/accepted.html")
        assert checks[year]["program_url"].endswith(f"/sosp/{year}/schedule.html")
    page = html_render.render_conference_papers_html(Config(), content)
    assert page.count('id="paper-sosp26-cpu-virtualization"') == 1


def test_sosp_backfill_reaches_year_facets_and_search_without_starring_new_records():
    content = conferences.load_content()
    papers = {p["id"]: p for p in content["papers"]}
    decisions = {p["id"]: p for p in content["screening"]["papers"]}
    documents = {d["slug"]: d for d in editorial_search.catalogue()["documents"]
                 if d["kind"] == "papers"}
    page = html_render.render_conference_papers_html(Config(), content)
    additions = {
        2025: ("demeter", "rdma-live-migration", "dandelion", "tai-chi"),
        2026: ("bpf-fault", "volistorm"),
    }
    for year, slugs in additions.items():
        for slug in slugs:
            pid = f"sosp{year % 100}-{slug}"
            paper, decision = papers[pid], decisions[pid]
            assert (paper["venue"], paper["year"], paper["curated"]) == ("sosp", year, False)
            assert (decision["origin"], decision["decision"]) == ("added", "keep")
            assert paper["evidence_source_url"] == decision["source_url"]
            assert decision["review_basis"] in {"primary_abstract", "pdf_sections", "author_project"}
            assert paper["doi"] and paper["url"] == f'https://doi.org/{paper["doi"]}'
            assert documents[pid]["href"] == f"conference-papers.html#paper-{pid}"
            row = page.split(f'id="paper-{pid}"', 1)[1].split("</article>", 1)[0]
            assert f'data-venue="sosp" data-year="{year}"' in row
    for pid in ("sosp26-skill-vm", "sosp25-oasis", "sosp25-coyote-v2", "sosp25-mufork",
                "sosp25-phoenixos", "sosp25-nex-dsim", "sosp25-pkvm-oracle", "sosp25-miralis"):
        assert decisions[pid]["decision"] == "exclude"
        assert pid not in papers and pid not in documents
        assert f'id="paper-{pid}"' not in page


def test_publication_badges_are_not_presented_as_a_quality_rating():
    content = conferences.load_content()
    page = html_render.render_conference_papers_html(Config(), content)
    block = page.split('id="paper-atc05-qemu"', 1)[1].split("</article>", 1)[0]
    assert "已发表" not in block and "已录用" not in block
    assert "<strong>点评</strong>" in block and ">PDF</a>" in block
    assert "入选理由" not in block and "评估与证据" not in block
    timeline = html_render.render_academic_conferences_html(Config(), content)
    assert 'href="conference-papers.html">查看相关论文</a>' not in timeline
    assert 'href="conference-papers.html?scope=all&amp;year=2026"' in timeline
