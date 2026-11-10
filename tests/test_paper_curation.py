"""Curation is an explicit, source-backed layer, not a publication-status badge."""
from copy import deepcopy
import re

import pytest

from virt_report import conferences, editorial_search, mechanisms
from virt_report.config import Config
from virt_report.render import render


def test_selected_readings_are_small_explicit_and_complete():
    content = conferences.load_content()
    selected = content["curated_papers"]
    assert 10 <= len(selected) <= 20
    assert content["paper_count"] > len(selected)
    assert content["paper_count"] == len(selected) + len(content["regular_papers"])
    assert [p["year"] for p in selected] == sorted((p["year"] for p in selected), reverse=True)
    assert {"cacm74-popek-goldberg", "osdi02-esx-memory", "sosp03-xen",
            "nsdi05-live-migration", "asplos06-software-hardware",
            "asplos14-kvm-arm", "atc14-gvirt"} <= {p["id"] for p in selected}
    known = {a["slug"] for a in mechanisms.articles()}
    for paper in selected:
        reading = paper["reading"]
        assert reading["reason"] and reading["evaluation"] and reading["limitations"]
        assert reading["source_url"].startswith("https://")
        assert set(reading["related_articles"]) <= known
        assert reading["reviewed_on"] == "2026-11-10"
    orthus = next(p for p in selected if p["id"] == "asplos19-orthus")["reading"]
    assert orthus["source_kind"] == "primary_excerpt"
    assert "未完成全文" in orthus["source_note"]
    assert orthus["support_url"].startswith("https://www.alibabacloud.com/")
    assert any(p["representative"] and not p["curated"] for p in content["papers"])
    assert all(not p["curated"] for p in content["papers"] if p["year"] == 2026)


@pytest.mark.parametrize("field", [
    "reason", "mechanism", "evaluation", "strengths", "limitations", "guide",
])
def test_curation_rejects_incomplete_reviews(field):
    content = deepcopy(conferences.load_content())
    content["reading_selection"]["papers"]["atc05-qemu"][field] = ""
    with pytest.raises(ValueError, match="缺少完整解读"):
        conferences._apply_reading_selection(content)


def test_curation_rejects_unknown_records_links_and_unqualified_excerpts():
    content = deepcopy(conferences.load_content())
    reviews = content["reading_selection"]["papers"]
    reviews["missing"] = deepcopy(reviews["atc05-qemu"])
    with pytest.raises(ValueError, match="未知论文"):
        conferences._apply_reading_selection(content)
    del reviews["missing"]
    reviews["atc05-qemu"]["related_articles"] = ["nonexistent"]
    with pytest.raises(ValueError, match="延伸阅读"):
        conferences._apply_reading_selection(content)
    reviews["atc05-qemu"]["related_articles"] = ["qemu-tcg"]
    reviews["atc05-qemu"]["source_url"] = "http://example.com/paper.pdf"
    with pytest.raises(ValueError, match="HTTPS"):
        conferences._apply_reading_selection(content)
    reviews["atc05-qemu"]["source_url"] = "https://example.com/paper.pdf"
    del reviews["asplos19-orthus"]["source_note"]
    with pytest.raises(ValueError, match="证据边界"):
        conferences._apply_reading_selection(content)


def test_papers_have_compact_title_resources_and_one_classic_filter():
    content = conferences.load_content()
    before = deepcopy(content)
    html = render.render_conference_papers_html(Config(), content)
    assert content == before
    rows = re.findall(r'<article class="paper-row".*?</article>', html, re.S)
    assert len(rows) == content["paper_count"]
    assert 'data-conference-additional' not in html
    assert html.count('data-curated="true"') == content["curated_count"]
    assert [int(re.search(r'data-year="(\d+)"', row)[1]) for row in rows] == sorted(
        (paper["year"] for paper in content["papers"]), reverse=True,
    )
    for row in rows:
        if 'data-curated="true"' in row:
            heading = row.split("<h2>", 1)[1].split("</h2>", 1)[0]
            assert 'class="paper-resource-tag"' in heading and ">PDF</a>" in heading
            assert "<strong>内容</strong>" in row and "<strong>点评</strong>" in row
        assert 'class="relation' not in row and 'class="tag representative' not in row
        assert not re.search(r'<p class="paper-evidence">[^<]*已发表', row)
    assert "<noscript>" in html
    assert html.count('data-conference-classic aria-checked="false"') == 1
    assert html.count('role="switch"') == 1
    assert html.index('data-conference-year') < html.index('data-conference-classic')
    assert f'共 {content["paper_count"]} 篇论文，其中 {content["curated_count"]} 篇经典论文。' in html
    for removed in ("paper-policy", "paper-reading", "paper-resources", "data-conference-scope",
                    "data-conference-count", "入选理由", "精选标准与阅读方法", "原文阅读位置",
                    "今天怎样读", "站内延伸", "核对日期", "作者项目说明", "摘要与来源"):
        assert removed not in html
    assert 'id="paper-sosp26-cpu-virtualization"' not in html
    assert "移除 5 篇" not in html and "6 篇因证据不足" not in html


def test_editorial_search_retains_ordinary_papers_and_indexes_deep_reviews():
    editorial_search._editorial_documents.cache_clear()
    documents = editorial_search._editorial_documents()
    papers = [d for d in documents if d["kind"] == "papers"]
    assert len(papers) == conferences.load_content()["paper_count"]
    assert any(p["slug"] == "asplos21-reap" for p in papers)
    esx = next(p for p in papers if p["slug"] == "osdi02-esx-memory")
    assert "双重换页" in esx["keywords"]
    assert esx["href"].endswith("#paper-osdi02-esx-memory")
    removed = {p["id"] for p in conferences.load_content()["screening"]["papers"]
               if p["decision"] != "keep"}
    assert removed.isdisjoint({p["slug"] for p in papers})


def test_every_original_and_added_paper_has_an_individual_screening_decision():
    content = conferences.load_content()
    audit = content["screening"]["papers"]
    assert len(audit) == 123
    assert sum(p["origin"] == "existing" for p in audit) == 116
    assert sum(p["origin"] == "added" for p in audit) == 7
    assert len({p["reason"] for p in audit}) == len(audit)
    assert {p["id"] for p in audit if p["decision"] == "keep"} == {
        p["id"] for p in content["papers"]
    }
    assert content["screening_counts"] == {"keep": 112, "exclude": 5, "defer": 6}
    assert not any(p["evidence_level"] == "title" for p in content["papers"])


def test_screening_rejects_unreviewed_or_reintroduced_papers():
    content = deepcopy(conferences.load_content())
    content["papers"][0]["id"] = "unreviewed-new-paper"
    with pytest.raises(ValueError, match="保留集合不一致"):
        conferences._validate_screening(content)
    content = deepcopy(conferences.load_content())
    content["screening"]["papers"][0]["review_basis"] = "title"
    with pytest.raises(ValueError, match="仅有标题"):
        conferences._validate_screening(content)
    content["screening"]["papers"][0]["reason"] = ""
    with pytest.raises(ValueError, match="缺少决定"):
        conferences._validate_screening(content)


def test_screening_cannot_silently_reuse_an_id_for_different_research():
    content = deepcopy(conferences.load_content())
    content["papers"][0]["title"] = "A different research contribution"
    with pytest.raises(ValueError, match="需要重新核对"):
        conferences._validate_screening(content)


@pytest.mark.parametrize("audit", [[], {"papers": {}}, {"papers": [None], "reviewed_on": "2026-11-10"}])
def test_screening_requires_well_formed_individual_records(audit):
    content = deepcopy(conferences.load_content())
    content["screening"] = audit
    with pytest.raises(ValueError, match="逐篇记录|有效 id"):
        conferences._validate_screening(content)
