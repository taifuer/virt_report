"""Sandbox-related additions preserve conference and evidence boundaries."""

import pytest

from virt_report import conferences
from virt_report.config import Config
from virt_report.render import render


EXPECTED_INSTITUTIONS = {
    "atc26-deltasandbox": [
        "Shanghai Jiao Tong University", "Huawei Technologies",
    ],
    "atc22-rund": ["Shanghai Jiao Tong University", "Alibaba Group"],
    "asplos21-reap": [
        "University of Edinburgh", "Microsoft Research", "EPFL",
    ],
}


def paper_block(page: str, paper_id: str) -> str:
    return page.split(f'id="paper-{paper_id}"', 1)[1].split("</article>", 1)[0]


@pytest.mark.parametrize("paper_id", EXPECTED_INSTITUTIONS)
def test_sandbox_additions_have_unique_verified_institutions(paper_id):
    content = conferences.load_content()
    matches = [paper for paper in content["papers"] if paper["id"] == paper_id]
    assert len(matches) == 1
    paper = matches[0]
    assert paper["institutions"] == EXPECTED_INSTITUTIONS[paper_id]
    assert len(paper["institutions"]) == len(set(paper["institutions"]))
    assert paper["affiliation_verified_at"] == "2026-11-02"
    assert paper["affiliation_source_url"].startswith("https://")
    assert paper["evidence_source_url"].startswith("https://")
    assert paper["introduction"] and paper["commentary"]
    assert not paper.get("abstract")

    page = render.render_conference_papers_html(Config(), content)
    block = paper_block(page, paper_id)
    assert 'data-conference-item' in block
    assert f'data-venue="{paper["venue"]}"' in block
    assert f'data-year="{paper["year"]}"' in block
    assert 'class="paper-credit"' in block
    assert "单位元数据来源：" in block
    for institution in paper["institutions"]:
        assert institution in block  # Search also indexes institution text.
    assert paper["institution_label"] in block
    assert not paper["institution_hidden"]


def test_deltasandbox_is_accepted_process_checkpoint_work_with_project_evidence():
    content = conferences.load_content()
    paper = next(p for p in content["papers"] if p["id"] == "atc26-deltasandbox")
    assert paper["year"] == 2026 and paper["venue"] == "atc"
    assert paper["title"].startswith("δSandbox:")
    assert paper["publication_status"] == "accepted"
    assert not paper.get("published_on")
    assert paper["evidence_level"] == "project"
    assert paper["relation"] == "相关机制"
    assert paper["url"] == "https://atc.sigops.org/2026/accepted.html"
    assert paper["evidence_source_url"] == "https://dongyunpeng-sjtu.github.io/deltabox/"
    assert paper["artifact_source_url"] == "https://sysartifacts.github.io/atc2026/results"
    assert paper["project_title"].startswith("DeltaBox:")
    assert "DeltaBox" in paper["introduction"]
    assert "DeltaFS" in paper["introduction"] and "DeltaCR" in paper["introduction"]
    assert "进程" in paper["introduction"] and "文件" in paper["introduction"]
    assert "Agent 沙箱" in paper["topics"]
    assert "KVM" not in paper["topics"] and "microVM" not in paper["topics"]
    assert paper["architectures"] == []

    page = render.render_conference_papers_html(Config(), content)
    block = paper_block(page, paper["id"])
    assert "已录用" in block and "已发表" not in block
    assert "作者项目说明" in block and "待摘要补充" not in block
    assert f'href="{paper["evidence_source_url"]}"' in block
    assert 'data-paper-topic="Agent 沙箱"' in block


@pytest.mark.parametrize("paper_id", ["asplos21-reap", "atc22-rund"])
def test_serverless_papers_retain_published_abstract_based_identity(paper_id):
    content = conferences.load_content()
    paper = next(p for p in content["papers"] if p["id"] == paper_id)
    assert paper["publication_status"] == "published"
    assert paper["evidence_level"] == "abstract"
    assert paper["relation"] == "直接关联"
    assert "Serverless" in paper["topics"] and "microVM" in paper["topics"]
    assert not any("Agent" in topic for topic in paper["topics"])
    page = render.render_conference_papers_html(Config(), content)
    block = paper_block(page, paper_id)
    assert "已发表" in block and "已录用" not in block
    assert "作者项目说明" not in block and "待摘要补充" not in block


def test_reap_records_publisher_affiliations_and_primary_abstract():
    content = conferences.load_content()
    paper = next(p for p in content["papers"] if p["id"] == "asplos21-reap")
    assert paper["doi"] == "10.1145/3445814.3446714"
    assert paper["affiliation_source"] == "crossref"
    assert paper["affiliation_source_url"] == (
        "https://api.crossref.org/works/10.1145/3445814.3446714"
    )
    assert paper["publication_source_url"] == paper["affiliation_source_url"]
    assert paper["published_on"] == "2021-04-17"
    assert paper["evidence_source_url"].startswith("https://www.research.ed.ac.uk/")
    assert "Firecracker" in paper["introduction"]
    assert "工作集" in paper["introduction"] and "预取" in paper["introduction"]


@pytest.mark.parametrize("year, name", [(2021, "REAP"), (2022, "RunD"), (2026, "δSandbox")])
def test_sandbox_additions_are_reflected_in_annual_reviews_and_counts(year, name):
    content = conferences.load_content()
    review = next(item for item in content["analysis"]["years"] if item["year"] == year)
    assert name in review["summary"]
    count = sum(paper["year"] == year for paper in content["papers"])
    assert review["paper_count"] == content["year_counts"][year] == count
    assert set(review["representative_ids"]) <= {p["id"] for p in content["papers"]}


def test_unconfirmed_tide_preprint_is_not_in_conference_archive():
    content = conferences.load_content()
    assert not any(
        "tide: reclaiming phased memory" in paper["title"].lower()
        or paper["id"].endswith("-tide")
        for paper in content["papers"]
    )
