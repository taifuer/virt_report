"""Source-backed surveys and books share discovery, not publication types."""
from copy import deepcopy
from html import unescape
import re

import pytest

from virt_report import conferences, editorial_search
from virt_report.config import Config
from virt_report.render import render


ADDITIONS = [
    ("csur24-cxl", 2024, "csur", "survey", "10.1145/3669900"),
    ("comst24-network-acceleration", 2024, "comst", "survey", "10.1109/COMST.2024.3377531"),
    ("csur23-vm-interference", 2023, "csur", "survey", "10.1145/3573009"),
    ("csur24-transient-execution", 2024, "csur", "survey", "10.1145/3603619"),
    ("slca20-memory-consistency", 2020, "slca", "monograph", "10.1007/978-3-031-01764-3"),
]


@pytest.mark.parametrize("paper_id,year,venue,kind,doi", ADDITIONS)
def test_recent_surveys_have_primary_sources_and_explicit_types(paper_id, year, venue, kind, doi):
    content = conferences.load_content()
    paper = next(p for p in content["papers"] if p["id"] == paper_id)
    audit = next(p for p in content["screening"]["papers"] if p["id"] == paper_id)
    assert (paper["year"], paper["venue"], paper["publication_type"]) == (year, venue, kind)
    assert paper["doi"] == doi and paper["url"] == f"https://doi.org/{doi}"
    assert paper["authors"] and paper["institutions"]
    assert paper["pdf_url"].startswith("https://")
    assert paper["evidence_source_url"] == audit["source_url"]
    assert paper["affiliation_verified_at"] == "2026-11-11"
    assert audit["origin"] == "added" and audit["decision"] == "keep"
    assert audit["review_basis"] != "title"
    assert not paper["curated"] and paper["reading"] is None
    assert sum(p.get("doi") == doi for p in content["papers"]) == 1
    type_label = "教程型专著" if kind == "monograph" else "综述"
    assert type_label in paper["topics"]

    page = render.render_conference_papers_html(Config(), content)
    row = re.search(rf'<article class="paper-row" id="paper-{paper_id}".*?</article>', page, re.S)[0]
    assert f'data-venue="{venue}" data-year="{year}"' in row
    assert f'data-publication-type="{kind}"' in row
    assert 'class="paper-classic-mark"' not in row
    assert f'href="{paper["url"]}"' in row and f'href="{paper["pdf_url"]}"' in row
    assert f'data-paper-topic="{type_label}"' in row
    assert len(re.findall(r'<p(?: class="[^"]*")?>', row)) == 2
    assert paper["introduction"] in unescape(row)
    assert paper["commentary"] in unescape(row)

    document = next(p for p in editorial_search.catalogue()["documents"] if p["slug"] == paper_id)
    assert document["kind"] == "papers" and type_label in document["headings"]
    assert document["href"] == f"conference-papers.html#paper-{paper_id}"


def test_book_counts_and_source_filters_do_not_label_monographs_as_papers():
    content = conferences.load_content()
    assert (content["paper_count"], content["article_count"], content["monograph_count"]) == (127, 126, 1)
    assert content["curated_count"] == 27
    venues = {venue["key"]: venue for venue in content["paper_venues"]}
    assert venues["csur"]["paper_count"] == 3 and venues["csur"]["kind"] == "期刊"
    assert venues["comst"]["paper_count"] == 1 and venues["comst"]["kind"] == "期刊"
    assert venues["slca"]["kind"] == "专著系列"
    assert venues["slca"]["count_label"] == "1 部专著"
    assert "slca" not in {venue["key"] for venue in content["academic_venues"]}
    assert {"csur", "comst", "slca"} <= {venue["key"] for venue in content["publication_venues"]}
    archive = render.render_conference_papers_html(Config(), content)
    assert "共 126 篇论文、1 部专著，其中 27 篇经典论文。" in archive
    assert "<label>来源<select data-conference-venue>" in archive
    for venue in ("csur", "comst", "slca"):
        assert f'<option value="{venue}"' in archive
    directory = render.render_conferences_html(Config(), content)
    assert 'venue=slca">1 部专著</a>' in directory
    assert "Synthesis Lectures on Computer Architecture" in directory
    assert "<h2>收录会议与出版物</h2>" in directory
    year = next(item for item in content["analysis"]["years"] if item["year"] == 2020)
    timeline = render.render_academic_conferences_html(Config(), content)
    assert f'{year["paper_count"] - 1} 篇论文 · 1 部专著' in timeline
    assert sum(content["year_counts"].values()) == content["paper_count"]


def test_surveys_keep_edition_years_and_technical_boundaries_explicit():
    papers = {p["id"]: p for p in conferences.load_content()["papers"]}
    transient = papers["csur24-transient-execution"]
    assert transient["published_online"] == "2023-10-06"
    assert "2024 年卷期" in transient["commentary"]
    assert "2023 年 10 月" in transient["commentary"]
    book = papers["slca20-memory-consistency"]
    assert book["edition"] == 2
    assert book["original_doi"] == "10.2200/S00962ED2V01Y201910CAC049"
    assert "2020 年第二版" in book["commentary"] and "2022 年" in book["commentary"]
    assert "DMA、MMIO" in book["commentary"]
    assert "截至 CXL 3.0" in papers["csur24-cxl"]["commentary"]
    assert "University of Washington" in papers["csur24-cxl"]["institutions"]
    assert "硬件、负载和测试条件" in papers["comst24-network-acceleration"]["commentary"]
    assert "采样开销" in papers["csur23-vm-interference"]["commentary"]


@pytest.mark.parametrize("field,value", [
    ("publication_type", "preprint"),
    ("publication_type", "research"),
    ("venue", "csur"),
])
def test_publication_type_must_match_the_publication_source(monkeypatch, field, value):
    content = deepcopy(conferences._read_content())
    book = next(p for p in content["papers"] if p["id"] == "slca20-memory-consistency")
    book[field] = value
    monkeypatch.setattr(conferences, "_read_content", lambda: content)
    conferences.load_content.cache_clear()
    try:
        with pytest.raises(ValueError, match="文献类型"):
            conferences.load_content()
    finally:
        conferences.load_content.cache_clear()


def test_archive_without_monographs_preserves_paper_counts(monkeypatch):
    content = deepcopy(conferences._read_content())
    book_id = "slca20-memory-consistency"
    content["papers"] = [p for p in content["papers"] if p["id"] != book_id]
    content["screening"]["papers"] = [p for p in content["screening"]["papers"] if p["id"] != book_id]
    monkeypatch.setattr(conferences, "_read_content", lambda: content)
    conferences.load_content.cache_clear()
    try:
        loaded = conferences.load_content()
        assert loaded["article_count"] == loaded["paper_count"] == 126
        assert loaded["monograph_count"] == 0
        page = render.render_conference_papers_html(Config(), loaded)
        assert "共 126 篇论文，其中 27 篇经典论文。" in page
        assert 'value="slca"' not in page
    finally:
        conferences.load_content.cache_clear()
