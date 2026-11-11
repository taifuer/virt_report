"""Curation is an explicit, source-backed layer, not a publication-status badge."""
from copy import deepcopy
from html import unescape
import re

import pytest

from virt_report import conferences, editorial_search, mechanisms
from virt_report.config import Config
from virt_report.render import render


def test_selected_readings_are_small_explicit_and_complete():
    content = conferences.load_content()
    selected = content["curated_papers"]
    assert 10 <= len(selected) <= 30
    assert content["paper_count"] > len(selected)
    assert content["paper_count"] == len(selected) + len(content["regular_papers"])
    assert [p["year"] for p in selected] == sorted((p["year"] for p in selected), reverse=True)
    assert {"cacm74-popek-goldberg", "osdi02-esx-memory", "sosp03-xen",
            "nsdi05-live-migration", "asplos06-software-hardware",
            "asplos14-kvm-arm", "atc14-gvirt", "osdi16-ingens"} <= {p["id"] for p in selected}
    known = {a["slug"] for a in mechanisms.articles()}
    for paper in selected:
        reading = paper["reading"]
        assert reading["reason"] and reading["evaluation"] and reading["limitations"]
        assert reading["source_url"].startswith("https://")
        assert set(reading["related_articles"]) <= known
        source = content["reading_selection"]["papers"][paper["id"]]
        assert reading["reviewed_on"] == source.get(
            "reviewed_on", content["reading_selection"]["reviewed_on"])
    orthus = next(p for p in selected if p["id"] == "asplos19-orthus")["reading"]
    assert orthus["source_kind"] == "pdf"
    assert not orthus.get("source_note")
    assert orthus["reviewed_on"] == "2026-11-11"
    assert "§4.1–4.4" in orthus["verified_sections"]
    assert "29–37" in orthus["evaluation"] and "32 GB" in orthus["evaluation"]
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
    reviews["atc05-qemu"]["source_kind"] = "primary_excerpt"
    reviews["atc05-qemu"].pop("source_note", None)
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
        heading = row.split("<h2>", 1)[1].split("</h2>", 1)[0]
        if 'data-curated="true"' in row:
            assert 'class="paper-classic-mark"' in heading
            assert heading.index('class="paper-classic-mark"') < heading.index('<a ')
            assert 'class="sr-only">经典论文：</span>' in heading
            assert 'aria-hidden="true" focusable="false"' in heading
            assert 'class="paper-resource-tag"' in heading and ">PDF</a>" in heading
            assert "<strong>内容</strong>" in row and "<strong>点评</strong>" in row
        else:
            assert 'class="paper-classic-mark"' not in heading
        assert 'class="relation' not in row and 'class="tag representative' not in row
        assert not re.search(r'<p class="paper-evidence">[^<]*已发表', row)
    assert "<noscript>" in html
    assert html.count('data-conference-filter="curated" aria-pressed="false"') == 1
    assert html.count('data-conference-filter="surveys" aria-pressed="false"') == 1
    assert 'role="switch"' not in html and 'paper-switch-track' not in html
    assert 'aria-label="快捷筛选"' in html
    assert '>经典</button>' in html and '>综述</button>' in html
    assert 'title="综述与教程型专著"' in html
    assert html.count('class="paper-reset-slot"') == 1
    assert html.count('data-conference-sort') == 1
    assert '>最新优先</option>' in html and '>最早优先</option>' in html
    assert html.index('data-conference-year') < html.index('data-conference-filter="curated"')
    assert (f'共 {content["article_count"]} 篇论文、{content["monograph_count"]} 部专著，'
            f'其中 {content["curated_count"]} 篇经典论文。') in html
    for removed in ("paper-reading", "阅读指引", "paper-policy", "paper-resources", "data-conference-scope",
                    "data-conference-count", "入选理由", "精选标准与阅读方法", "原文阅读位置",
                    "今天怎样读", "站内延伸", "核对日期", "作者项目说明", "摘要与来源"):
        assert removed not in html
    assert 'id="paper-sosp26-cpu-virtualization"' not in html
    assert "移除 5 篇" not in html and "6 篇因证据不足" not in html


@pytest.mark.parametrize("paper_id,doi", [
    ("sosp24-vsoc", "10.1145/3694715.3695946"),
    ("sosp24-vpri", "10.1145/3694715.3695957"),
    ("asplos24-dmt", "10.1145/3620665.3640358"),
])
def test_architecture_papers_link_directly_to_the_publication(paper_id, doi):
    content = conferences.load_content()
    paper = next(item for item in content["papers"] if item["id"] == paper_id)
    assert paper["url"] == f"https://doi.org/{doi}"
    assert not paper["curated"]
    if paper_id in {"sosp24-vsoc", "asplos24-dmt"}:
        assert paper["pdf_url"].startswith("https://") and paper["pdf_url"].endswith(".pdf")
        assert paper["evidence_level"] == "abstract"


def test_classic_comments_include_evidence_without_fragmenting_the_row():
    content = conferences.load_content()
    html = render.render_conference_papers_html(Config(), content)
    for paper in content["curated_papers"]:
        reading = paper["reading"]
        row = re.search(
            rf'<article class="paper-row" id="paper-{re.escape(paper["id"])}".*?</article>',
            html, re.S,
        )[0]
        paragraphs = re.findall(r'<p(?: class="[^"]*")?>(.*?)</p>', row, re.S)
        # Keep only content, commentary and any necessary provenance note.
        assert len(paragraphs) == 2 + bool(reading.get("source_note"))
        assert unescape(reading["guide"]) not in unescape(row)
        assert 'class="paper-reading"' not in row
        comment = unescape(re.sub(r"<[^>]*>", "", paragraphs[1]))
        assert comment == (
            "点评" + reading["evaluation"] + " " + reading["strengths"]
            + " " + reading["limitations"]
        )
        assert "？" not in reading["question"]
    assert "未完成全文及实验表格复核" not in html


def test_ingens_is_a_source_backed_2016_memory_paper():
    content = conferences.load_content()
    ingens = next(p for p in content["papers"] if p["id"] == "osdi16-ingens")
    assert (ingens["year"], ingens["venue"]) == (2016, "osdi")
    assert ingens["curated"] and ingens["evidence_level"] == "full_text"
    assert ingens["url"] == "https://www.usenix.org/conference/osdi16/technical-sessions/presentation/kwon"
    assert ingens["pdf_url"] == ingens["reading"]["source_url"]
    assert len(ingens["authors"]) == 5
    evaluation = ingens["reading"]["evaluation"]
    for condition in ("4.3", "Haswell", "Cloudstone", "碎片", "两个网页", "18%", "0.7%"):
        assert condition in evaluation


def test_linux_scheduler_is_an_explicit_source_backed_kernel_classic():
    content = conferences.load_content()
    paper = next(p for p in content["papers"] if p["id"] == "eurosys16-linux-scheduler")
    assert (paper["year"], paper["venue"], paper["domain"]) == (2016, "eurosys", "kernel")
    assert paper["curated"] and paper["evidence_level"] == "full_text"
    assert paper["url"] == "https://doi.org/10.1145/2901318.2901326"
    assert paper["doi"] == "10.1145/2901318.2901326"
    assert paper["pdf_url"] == paper["reading"]["source_url"]
    assert len(paper["authors"]) == 6 and len(paper["institutions"]) == 5
    reading = paper["reading"]
    assert reading["source_kind"] == "pdf" and "§4.1–4.2" in reading["verified_sections"]
    for term in ("64 核", "8 个 NUMA", "Opteron", "make/R", "13%", "3.17", "4.3"):
        assert term in reading["evaluation"]
    for term in ("负载度量", "调度组", "唤醒选核", "热插拔", "四类", "可视化"):
        assert term in reading["mechanism"]
    assert "CFS" in reading["limitations"] and "EEVDF" in reading["limitations"]
    assert "linux-execution-scheduling" in reading["related_articles"]
    page = render.render_mechanisms_html(Config(), "linux-execution-scheduling")
    assert 'href="../conference-papers.html#paper-eurosys16-linux-scheduler"' in page


def test_kernel_scope_is_limited_to_individually_reviewed_classics():
    content = conferences.load_content()
    kernel = [paper for paper in content["papers"] if paper.get("domain") == "kernel"]
    assert {paper["id"] for paper in kernel} == {
        "cacm74-unix", "eurosys13-radixvm", "eurosys16-linux-scheduler",
        "systor13-blk-mq", "atc12-netmap", "asplos20-mitosis",
    }
    assert all(paper["curated"] and paper["reading"]["source_kind"] == "pdf" for paper in kernel)
    altered = deepcopy(content)
    del altered["reading_selection"]["papers"]["eurosys13-radixvm"]
    with pytest.raises(ValueError, match="内核方向仅收录经典论文"):
        conferences._apply_reading_selection(altered)


def test_mitosis_preserves_page_table_scope_and_numa_measurement_conditions():
    content = conferences.load_content()
    paper = next(p for p in content["papers"] if p["id"] == "asplos20-mitosis")
    assert (paper["year"], paper["venue"], paper["domain"]) == (2020, "asplos", "kernel")
    assert paper["curated"] and paper["evidence_level"] == "full_text"
    assert paper["doi"] == "10.1145/3373376.3378468"
    assert paper["url"] == f'https://doi.org/{paper["doi"]}'
    assert paper["pdf_url"] == "https://retoachermann.ch/static/papers/achermann-2020-mts.pdf"
    reading = paper["reading"]
    assert paper["pdf_url"] == reading["source_url"]
    assert len(paper["authors"]) == 5 and len(paper["institutions"]) == 4
    for term in ("PV-Ops", "页表根", "应用数据", "访问位", "脏位"):
        assert term in reading["mechanism"]
    for term in ("4.17", "四插槽", "E7-4850v3", "512 GB", "4 KiB", "Canneal", "1.34×"):
        assert term in reading["evaluation"]
    for term in ("额外内存", "映射更新", "进程页表", "EPT/NPT"):
        assert term in reading["limitations"]
    assert "§8.1–8.3" in reading["verified_sections"]
    page = render.render_mechanisms_html(Config(), "linux-memory")
    assert 'href="../conference-papers.html#paper-asplos20-mitosis"' in page


def test_shinjuku_preserves_virtualization_mechanism_and_experiment_boundaries():
    content = conferences.load_content()
    paper = next(p for p in content["papers"] if p["id"] == "nsdi19-shinjuku")
    assert (paper["year"], paper["venue"]) == (2019, "nsdi")
    assert paper["curated"] and paper["relation"] == "直接关联"
    assert paper["evidence_level"] == "full_text"
    assert paper["url"] == "https://www.usenix.org/conference/nsdi19/presentation/kaffes"
    assert paper["pdf_url"] == "https://www.usenix.org/system/files/nsdi19-kaffes.pdf"
    assert len(paper["authors"]) == 6 and len(paper["institutions"]) == 2
    reading = paper["reading"]
    assert reading["source_url"] == paper["pdf_url"]
    for term in ("Dune", "posted interrupts", "可信调度器", "APIC", "VM-exit", "重新入队"):
        assert term in reading["mechanism"]
    for term in ("内存型", "RocksDB 5.13", "99.5%", "0.5%", "SCAN(1000)", "15 μs", "ZygOS"):
        assert term in reading["evaluation"]
    for term in ("专用核", "同地址空间", "UDP", "TCP"):
        assert term in reading["limitations"]
    assert "§4.1–4.4" in reading["verified_sections"]
    page = render.render_mechanisms_html(Config(), "linux-execution-scheduling")
    assert 'href="../conference-papers.html#paper-nsdi19-shinjuku"' in page


def test_removed_early_kernel_papers_leave_no_public_entries_or_article_links():
    content = conferences.load_content()
    removed = {"cacm68-working-set", "osdi94-lottery"}
    assert removed.isdisjoint({paper["id"] for paper in content["papers"]})
    assert removed.isdisjoint(content["reading_selection"]["papers"])
    audit = {paper["id"]: paper for paper in content["screening"]["papers"]}
    for paper_id in removed:
        assert audit[paper_id]["decision"] == "exclude"
        assert "按用户要求移除" in audit[paper_id]["reason"]
    pages = [
        render.render_conference_papers_html(Config(), content),
        render.render_academic_conferences_html(Config(), content),
        render.render_mechanisms_html(Config(), "linux-execution-scheduling"),
        render.render_mechanisms_html(Config(), "linux-memory"),
    ]
    for page in pages:
        for paper_id in removed:
            assert paper_id not in page
            assert audit[paper_id]["title"] not in page


def test_editorial_search_retains_ordinary_papers_and_indexes_deep_reviews():
    editorial_search._editorial_documents.cache_clear()
    documents = editorial_search._editorial_documents()
    papers = [d for d in documents if d["kind"] == "papers"]
    assert len(papers) == conferences.load_content()["paper_count"]
    assert any(p["slug"] == "asplos21-reap" for p in papers)
    esx = next(p for p in papers if p["slug"] == "osdi02-esx-memory")
    assert "双重换页" in esx["keywords"]
    assert esx["href"].endswith("#paper-osdi02-esx-memory")
    ingens = next(p for p in papers if p["slug"] == "osdi16-ingens")
    assert "Cloudstone" in ingens["keywords"] and "0.7%" in ingens["keywords"]
    scheduler = next(p for p in papers if p["slug"] == "eurosys16-linux-scheduler")
    assert "运行队列" in scheduler["keywords"] and "13%" in scheduler["keywords"]
    mitosis = next(p for p in papers if p["slug"] == "asplos20-mitosis")
    assert "页表局部性" in mitosis["keywords"] and "PV-Ops" in mitosis["keywords"]
    shinjuku = next(p for p in papers if p["slug"] == "nsdi19-shinjuku")
    assert "posted interrupts" in shinjuku["keywords"] and "RocksDB" in shinjuku["keywords"]
    removed = {p["id"] for p in conferences.load_content()["screening"]["papers"]
               if p["decision"] != "keep"}
    assert removed.isdisjoint({p["slug"] for p in papers})


def test_every_original_and_added_paper_has_an_individual_screening_decision():
    content = conferences.load_content()
    audit = content["screening"]["papers"]
    assert len(audit) == 140
    assert sum(p["origin"] == "existing" for p in audit) == 116
    assert sum(p["origin"] == "added" for p in audit) == 24
    assert len({p["reason"] for p in audit}) == len(audit)
    assert {p["id"] for p in audit if p["decision"] == "keep"} == {
        p["id"] for p in content["papers"]
    }
    assert content["screening_counts"] == {"keep": 127, "exclude": 7, "defer": 6}
    assert not any(p["evidence_level"] == "title" for p in content["papers"])


def test_io_classics_preserve_primary_sources_and_measurement_boundaries():
    content = conferences.load_content()
    papers = {paper["id"]: paper for paper in content["papers"]}
    block, net = papers["systor13-blk-mq"], papers["atc12-netmap"]
    assert (block["year"], block["venue"]) == (2013, "systor")
    assert block["url"] == "https://doi.org/10.1145/2485732.2485740"
    assert block["pdf_url"] == "https://www.kernel.dk/blk-mq.pdf"
    assert len(block["authors"]) == 4
    assert block["institutions"] == ["IT University of Copenhagen", "Fusion-io"]
    assert (net["year"], net["venue"]) == (2012, "atc")
    assert net["url"] == "https://www.usenix.org/conference/atc12/technical-sessions/presentation/rizzo"
    assert net["authors"] == ["Luigi Rizzo"]
    for paper in (block, net):
        assert paper["domain"] == "kernel" and paper["curated"]
        assert paper["pdf_url"] == paper["reading"]["source_url"]
        assert paper["reading"]["verified_sections"]
    for term in ("3.10", "fio", "null", "单插槽", "350 万"):
        assert term in block["reading"]["evaluation"]
    assert "不传输数据" in block["reading"]["limitations"]
    for term in ("FreeBSD", "82599", "64 B", "900 MHz", "1488 万"):
        assert term in net["reading"]["evaluation"]
    assert "上述数字来自 FreeBSD" in net["reading"]["limitations"]
    assert "linux-block-io" in block["reading"]["related_articles"]
    assert "linux-network-io" in net["reading"]["related_articles"]
    html = render.render_conference_papers_html(Config(), content)
    assert "立即完成请求的 null 设备" in html
    assert "基准预先生成报文或只计数接收" in html
    assert 'data-option-label="SYSTOR"' in html


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
