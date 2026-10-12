"""Keep publication status independent from the available review evidence."""

from virt_report import conferences, kvm_forum
from virt_report.config import Config
from virt_report.render import render as html_render


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
    assert "不代表方案已经合入、完成验证" in preview["analysis"]["summary"]

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
