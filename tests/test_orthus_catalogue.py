"""Keep Orthus discoverable and link reviewed papers to their publishers."""

import pytest

from virt_report import conferences, db
from virt_report.config import Config
from virt_report.render import render


ORTHUS_TITLE = "Fast and Scalable VMM Live Upgrade in Large Cloud Infrastructure"
ORTHUS_DOI = "10.1145/3297858.3304034"


def test_orthus_has_verified_publication_metadata_and_review_links():
    content = conferences.load_content()
    paper = next(p for p in content["papers"] if p["id"] == "asplos19-orthus")
    assert paper["title"] == ORTHUS_TITLE
    assert paper["venue"] == "asplos" and paper["year"] == 2019
    assert paper["doi"] == ORTHUS_DOI
    assert paper["url"] == f"https://doi.org/{ORTHUS_DOI}"
    assert paper["publication_status"] == "published"
    assert paper["published_on"] == "2019-04-04"
    assert len(paper["authors"]) == 7
    assert set(paper["institutions"]) == {
        "Alibaba Group", "Florida State University", "Tsinghua University",
    }
    assert paper["publication_source_url"] == (
        f"https://api.crossref.org/works/{ORTHUS_DOI}"
    )
    assert "Dual KVM" in paper["topics"]
    assert "VM grafting" in paper["introduction"]
    topic = next(t for t in content["topic_links"] if t["key"] == "live-upgrade")
    year = next(y for y in content["analysis"]["years"] if y["year"] == 2019)
    assert "asplos19-orthus" in topic["paper_ids"]
    assert "asplos19-orthus" in year["representative_ids"]
    page = render.render_conference_papers_html(Config(), content)
    block = page.split('id="paper-asplos19-orthus"', 1)[1].split("</article>", 1)[0]
    assert f'href="https://doi.org/{ORTHUS_DOI}"' in block
    assert ORTHUS_TITLE in block and "Dual KVM" in block
    assert "10.1145/1122445.1122456" not in block  # Author draft placeholder DOI.


def test_orthus_is_a_candidate_until_an_editor_review_is_imported(tmp_path):
    assert conferences.is_candidate_title(ORTHUS_TITLE)
    pid = conferences.paper_id("asplos", 2019, ORTHUS_TITLE)
    conn = db.connect(tmp_path / "papers.db")
    conn.execute("""INSERT INTO conference_papers
        (paper_id,venue,year,title,doi,official_url,fetched_at)
        VALUES (?,?,?,?,?,?,?)""", (
        pid, "asplos", 2019, ORTHUS_TITLE, ORTHUS_DOI,
        f"https://doi.org/{ORTHUS_DOI}", "2026-11-03",
    ))
    conn.commit()
    assert [p["paper_id"] for p in conferences.candidate_rows(conn)] == [pid]
    paper = next(p for p in conferences.load_content()["papers"]
                 if p["id"] == "asplos19-orthus")
    assert conferences.import_editor_reviews(conn, {"papers": [paper]}) == 1
    assert conferences.candidate_rows(conn) == []
    row = conn.execute("SELECT relevance FROM conference_reviews WHERE paper_id=?",
                       (pid,)).fetchone()
    assert row["relevance"] == "included"
    conn.close()


@pytest.mark.parametrize("links,expected_url,expected_doi", [
    (["https://author.example/paper.pdf", f"https://doi.org/{ORTHUS_DOI}"],
     f"https://doi.org/{ORTHUS_DOI}", ORTHUS_DOI),
    (["https://www.usenix.org/conference/example/presentation/paper"],
     "https://www.usenix.org/conference/example/presentation/paper", None),
    ([], "https://dblp.org/rec/conf/asplos/Example19", None),
])
def test_catalogue_prefers_supplied_doi_and_keeps_source_provenance(
    monkeypatch, links, expected_url, expected_doi,
):
    payload = (
        '<bht><inproceedings key="conf/asplos/Example19">'
        f"<title>{ORTHUS_TITLE}.</title>"
        + "".join(f"<ee>{url}</ee>" for url in links)
        + "</inproceedings></bht>"
    ).encode()
    monkeypatch.setattr(conferences, "_get_dblp_xml", lambda _s, _u: payload)
    records = conferences.fetch_dblp_edition(object(), "asplos", 2019)
    assert len(records) == 1
    assert records[0]["official_url"] == expected_url
    assert records[0]["doi"] == expected_doi
    assert records[0]["source_url"] == "https://dblp.org/rec/conf/asplos/Example19"


@pytest.mark.parametrize("paper_id,doi", [
    ("vee22-portkey", "10.1145/3516807.3516817"),
    ("vee19-live-replacement", "10.1145/3313808.3313821"),
    ("vee19-acrn", "10.1145/3313808.3313816"),
    ("vee19-teev", "10.1145/3313808.3313810"),
    ("vee18-gmig", "10.1145/3186411.3186414"),
    ("vee18-live-migration-scale", "10.1145/3186411.3186415"),
    ("vee18-demon", "10.1145/3186411.3186416"),
    ("vee16-kvm-hsa", "10.1145/2892242.2892246"),
    ("vee16-ept", "10.1145/2892242.2892258"),
    ("vee16-migration-optimizations", "10.1145/2892242.2892247"),
    ("vee16-urgent-eviction", "10.1145/2892242.2892252"),
    ("vee15-direct-interrupt", "10.1145/2731186.2731189"),
    ("vee15-rdma-io", "10.1145/2731186.2731200"),
    ("vee15-virtual-cluster", "10.1145/2731186.2731194"),
    ("vee14-introspection", "10.1145/2576195.2576196"),
    ("vee14-tesseract", "10.1145/2576195.2576198"),
    ("vee14-mortar", "10.1145/2576195.2576203"),
    ("vee12-dvm", "10.1145/2151024.2151032"),
    ("vee12-secondsite", "10.1145/2151024.2151039"),
    ("vee12-compsc", "10.1145/2151024.2151040"),
    ("vee11-rehype", "10.1145/1952682.1952692"),
    ("vee11-checkpoint", "10.1145/1952682.1952694"),
    ("vee11-cloudnet", "10.1145/1952682.1952699"),
])
def test_verified_papers_link_to_the_conference_doi(paper_id, doi):
    paper = next(p for p in conferences.load_content()["papers"]
                 if p["id"] == paper_id)
    assert paper["url"] == f"https://doi.org/{doi}"


def test_dvm_keeps_the_2012_conference_title():
    paper = next(p for p in conferences.load_content()["papers"]
                 if p["id"] == "vee12-dvm")
    assert paper["title"] == "DVM: Towards a Datacenter-Scale Virtual Machine"


def test_hyperfresh_is_discoverable_with_its_nested_replacement_boundary():
    content = conferences.load_content()
    paper = next(p for p in content["papers"] if p["id"] == "vee19-live-replacement")
    assert paper["title"] == "Fast and Live Hypervisor Replacement"
    assert paper["url"] == "https://doi.org/10.1145/3313808.3313821"
    assert {"HyperFresh", "KVM", "嵌套虚拟化"} <= set(paper["topics"])
    for phrase in ("L0 hyperplexor", "L1 Hypervisor", "APSys 2017", "多虚机"):
        assert phrase in paper["introduction"]
    assert "L0 保留" in paper["commentary"]
    assert "暂停数字直接排名" in paper["commentary"]
    page = render.render_conference_papers_html(Config(), content)
    block = page.split('id="paper-vee19-live-replacement"', 1)[1].split("</article>", 1)[0]
    assert "HyperFresh" in block and "嵌套虚拟化" in block
