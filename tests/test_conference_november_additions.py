"""Reviewed conference additions retain publication and evidence boundaries."""
import copy

import pytest

from virt_report import conferences
from virt_report.config import Config
from virt_report.render import render


ATC_IDS = {f"atc26-{name}" for name in ("fm2", "pave", "joseph", "chameleon", "courier")}
ABSTRACT_IDS = {
    "osdi26-ramryder", "nsdi26-wallet", "nsdi26-cstar",
    "security26-hyperaudit", "eurosys26-bask", "asplos26-arancini",
}
NEW_IDS = ABSTRACT_IDS | {"asplos26-cemu"}


def paper_block(page, pid):
    return page.split(f'id="paper-{pid}"', 1)[1].split("</article>", 1)[0]


def test_retained_additions_have_unique_ids_and_verified_institutions():
    content = conferences.load_content()
    papers = content["papers"]
    assert NEW_IDS <= {paper["id"] for paper in papers}
    for pid in NEW_IDS:
        matches = [paper for paper in papers if paper["id"] == pid]
        assert len(matches) == 1
        paper = matches[0]
        assert paper["year"] == 2026
        assert paper["institutions"]
        assert len(paper["institutions"]) == len(set(paper["institutions"]))
        assert paper["affiliation_verified_at"] == "2026-11-02"
        assert paper["affiliation_source_url"].startswith("https://")
        assert paper["evidence_source_url"].startswith("https://")
        assert paper["introduction"] and paper["commentary"]
        assert not paper.get("abstract")  # Store paraphrases, not copied abstracts.


def test_atc_tracking_is_separate_from_title_only_paper_admission():
    content = conferences.load_content()
    venue = next(item for item in content["venues"] if item["key"] == "atc")
    assert venue["name"] == "ATC"
    assert venue["latest_year"] == 2025
    assert "2025 年及以前为 USENIX ATC" in venue["focus"]
    assert "ACM SIGOPS" in venue["focus"]
    assert venue["latest_check"]["parser"] == "atc"
    assert venue["latest_check"]["source_url"] == "https://atc.sigops.org/2026/accepted.html"
    assert any(p["id"] == "atc25-hyperturtle" for p in content["papers"])
    page = render.render_conference_papers_html(Config(), content)
    assert ATC_IDS.isdisjoint(p["id"] for p in content["papers"])
    audit = {p["id"]: p for p in content["screening"]["papers"]}
    for pid in ATC_IDS:
        assert audit[pid]["decision"] == "defer"
        assert audit[pid]["review_basis"] == "title"
        assert f'id="paper-{pid}"' not in page


def test_abstracts_and_author_project_are_distinct_review_sources():
    content = conferences.load_content()
    by_id = {paper["id"]: paper for paper in content["papers"]}
    assert all(by_id[pid]["evidence_level"] == "abstract" for pid in ABSTRACT_IDS)
    assert by_id["eurosys26-bask"]["relation"] == "相关机制"
    assert by_id["asplos26-arancini"]["relation"] == "相关机制"
    assert by_id["security26-hyperaudit"]["architectures"] == ["x86", "Arm"]
    cemu = by_id["asplos26-cemu"]
    assert cemu["evidence_level"] == "project"
    assert "QEMU 8.0" in cemu["introduction"] and "FEMU" in cemu["introduction"]
    page = render.render_conference_papers_html(Config(), content)
    block = paper_block(page, cemu["id"])
    assert cemu["evidence_source_url"] == "https://github.com/cs-qyzhang/CEMU"
    assert "作者项目说明" not in block and "待摘要补充" not in block
    for pid in ("eurosys26-bask", "asplos26-arancini"):
        block = paper_block(page, pid)
        assert by_id[pid]["evidence_source_url"].startswith("https://")
        assert f'href="{by_id[pid]["url"]}"' in block
        assert "摘要与来源" not in block


def test_year_review_and_check_notes_reflect_scope_of_the_update():
    content = conferences.load_content()
    year = next(item for item in content["analysis"]["years"] if item["year"] == 2026)
    assert content["updated_at"] >= "2026-11-02"
    for name in ("RamRyder", "CStar", "Wallet", "HyperAudit", "ATC 2026"):
        assert name in year["summary"]
    assert "标题级候选暂不公开" in year["summary"]
    assert year["paper_count"] == content["year_counts"][2026]
    assert set(year["representative_ids"]) <= {p["id"] for p in content["papers"]}
    checks = {item["venue"]: item for item in content["edition_checks"]}
    assert "Round 1" in checks["socc"]["note"]
    assert "未完成总议程" in checks["usenix-security"]["note"]


@pytest.mark.parametrize("source", ["javascript:alert(1)", "http://example.org", None])
def test_project_provenance_requires_a_safe_source_link(monkeypatch, source):
    content = copy.deepcopy(conferences._read_content())
    cemu = next(paper for paper in content["papers"] if paper["id"] == "asplos26-cemu")
    cemu["evidence_source_url"] = source
    monkeypatch.setattr(conferences, "_read_content", lambda: content)
    conferences.load_content.cache_clear()
    try:
        with pytest.raises(ValueError, match="点评来源|必须提供来源"):
            conferences.load_content()
    finally:
        conferences.load_content.cache_clear()
