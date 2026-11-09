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
    for venue in content["academic_venues"]:
        assert f'<h3>{escape(venue["name"])}</h3>' in page
        assert f'<div class="venue-description"><p>{escape(venue["focus"])}</p></div>' in page
        assert (f'href="conference-papers.html?venue={venue["key"]}">'
                f'{venue["paper_count"]} 篇相关论文</a>') in page
        assert (f'href="{escape(venue["url"])}" target="_blank" '
                'rel="noopener noreferrer">官方网站</a>') in page
    timeline = html_render.render_academic_conferences_html(Config(), content)
    assert 'href="conferences.html#conference-sources">查看收录会议</a>' in timeline
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


def test_kvm_forum_preview_replaces_withdrawn_session_without_inflating_count():
    editions, analysis = kvm_forum.load_content()
    preview = next(item for item in editions if item["year"] == 2026)
    talks = {item["url"]: item for item in preview["talks"]}

    assert preview["preview"] is True
    assert preview["starts_on"] == "2026-11-12"
    assert preview["ends_on"] == "2026-11-13"
    assert preview["checked_at"] == "2026-10-12"
    assert len(talks) == len(preview["talks"]) == 43
    assert "https://pretalx.com/kvm-forum-2026/talk/KP3ZN7/" not in talks
    assert talks["https://pretalx.com/kvm-forum-2026/talk/BY8NR3/"]["title"] == (
        "Artificial Intuition: Lessons Learned From Vibe-Auditing KVM"
    )
    assert set(preview["selected_urls"]) <= talks.keys()
    assert "AI 辅助 KVM 安全审计" in preview["analysis"]["summary"]
    assert "依据会前议程归纳" in preview["analysis"]["summary"]
    assert "合入、验证和部署状态需以各项目后续记录为准" in preview["analysis"]["summary"]

    page = html_render.render_kvm_forum_html(Config(), editions, analysis)
    assert "43 个议题" in page
    assert "AI 辅助 KVM 安全审计" in page
    assert "Scheduling KVM Sandboxes" not in page


def test_sosp_publication_keeps_title_only_evidence_and_verified_sources():
    content = conferences.load_content()
    paper = next(item for item in content["papers"]
                 if item["id"] == "sosp26-cpu-virtualization")
    check = next(item for item in content["edition_checks"]
                 if item["venue"] == "sosp" and item["year"] == 2026)

    assert paper["publication_status"] == "published"
    assert paper["evidence_level"] == "title"
    assert paper["doi"] == "10.1145/3830418.3843895"
    assert paper["url"] == f'https://dl.acm.org/doi/{paper["doi"]}'
    assert paper["published_on"] == "2026-09-28"
    assert paper["publication_source_url"] == (
        f'https://api.crossref.org/works/{paper["doi"]}'
    )
    assert not paper.get("abstract")
    assert "尚未核实可用的公开摘要" in paper["introduction"]
    assert "不推断性能数字" in paper["commentary"]
    assert paper["institutions"] == [
        "Shanghai Jiao Tong University", "Alibaba Group",
    ]
    assert paper["affiliation_source_url"] == check["source_url"]
    assert check["source_url"].endswith("/sosp/2026/schedule.html")
    assert check["checked_at"] == paper["affiliation_verified_at"] == "2026-10-12"


def test_published_paper_renders_status_without_overstating_evidence():
    content = conferences.load_content()
    page = html_render.render_conference_papers_html(Config(), content)
    paper = page.split('id="paper-sosp26-cpu-virtualization"', 1)[1]
    paper = paper.split("</article>", 1)[0]

    assert 'href="https://dl.acm.org/doi/10.1145/3830418.3843895"' in paper
    assert "已发表" in paper
    assert "目前仅核实标题，待摘要补充" in paper
    assert "已录用" not in paper
    assert '<p class="paper-evidence"> ·' not in paper

    timeline = html_render.render_academic_conferences_html(Config(), content)
    assert "SOSP 的新 CPU 虚拟化论文已正式发表" in timeline
    assert "SOSP 录用名单出现" not in timeline
