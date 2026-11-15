"""Keep publication status independent from the available review evidence."""

from copy import deepcopy
from html import escape

from virt_report import conferences, kvm_forum
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
    for venue in content["publication_venues"]:
        assert f'<h3>{escape(venue["name"])}</h3>' in page
        assert f'<div class="venue-description"><p>{escape(venue["focus"])}</p></div>' in page
        assert (f'href="conference-papers.html?scope=all&amp;venue={venue["key"]}">'
                f'{venue["count_label"]}</a>') in page
        assert (f'href="{escape(venue["url"])}" target="_blank" '
                'rel="noopener noreferrer">官方网站</a>') in page
    timeline = html_render.render_academic_conferences_html(Config(), content)
    assert 'href="conferences.html#conference-sources">收录会议</a>' in timeline
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


def test_sosp_publication_does_not_override_title_only_admission_decision():
    content = conferences.load_content()
    assert not any(p["id"] == "sosp26-cpu-virtualization" for p in content["papers"])
    audit = next(p for p in content["screening"]["papers"]
                 if p["id"] == "sosp26-cpu-virtualization")
    assert audit["decision"] == "defer"
    assert audit["review_basis"] == "title"
    assert audit["source_url"] == "https://dl.acm.org/doi/10.1145/3830418.3843895"
    check = next(item for item in content["edition_checks"]
                 if item["venue"] == "sosp" and item["year"] == 2026)
    assert check["source_url"].endswith("/sosp/2026/schedule.html")
    assert "不因已发表自动收录" in check["note"]
    page = html_render.render_conference_papers_html(Config(), content)
    assert 'id="paper-sosp26-cpu-virtualization"' not in page


def test_publication_badges_are_not_presented_as_a_quality_rating():
    content = conferences.load_content()
    page = html_render.render_conference_papers_html(Config(), content)
    block = page.split('id="paper-atc05-qemu"', 1)[1].split("</article>", 1)[0]
    assert "已发表" not in block and "已录用" not in block
    assert "<strong>点评</strong>" in block and ">PDF</a>" in block
    assert "入选理由" not in block and "评估与证据" not in block
    timeline = html_render.render_academic_conferences_html(Config(), content)
    assert 'href="conference-papers.html">查看相关论文</a>' in timeline
