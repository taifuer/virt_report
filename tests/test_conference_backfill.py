"""Historical discovery must advance without silently publishing candidates."""
import json

import pytest
import requests

from virt_report import conferences, db


def _insert(conn, key, year=2012, venue="osdi", title="An Opaque Name"):
    conn.execute("""INSERT INTO conference_papers
        (paper_id,venue,year,title,doi,fetched_at) VALUES (?,?,?,?,?,?)""",
                 (key, venue, year, title, "10.1234/" + key, "2026-11-01"))
    conn.commit()


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    monkeypatch.setattr(conferences.time, "sleep", lambda _: None)
    with db.connect(tmp_path / "catalogue.db") as conn:
        yield conn


def test_abstract_discovery_includes_opaque_titles_and_advances_batches(catalogue, monkeypatch):
    _insert(catalogue, "old", 2012)
    _insert(catalogue, "new", 2026)
    calls = []

    def fetch(_session, url, **_kwargs):
        calls.append(url)
        return {"message": {"DOI": url.removeprefix("https://api.crossref.org/works/"),
                            "title": ["An Opaque Name"],
                            "abstract": "<p>We implement a hypervisor.</p>"}}

    monkeypatch.setattr(conferences, "_get_json", fetch)
    assert conferences.enrich_candidate_abstracts(catalogue, limit=1) == 1
    assert calls[0].endswith("/old")
    assert catalogue.execute("SELECT abstract FROM conference_papers WHERE paper_id='old'").fetchone()[0] == "We implement a hypervisor."
    assert conferences.candidate_rows(catalogue)[0]["paper_id"] == "old"
    assert conferences.enrich_candidate_abstracts(catalogue, limit=1) == 1
    assert calls[-1].endswith("/new")
    assert catalogue.execute("SELECT COUNT(*) FROM conference_reviews").fetchone()[0] == 0


def test_missing_abstracts_and_failures_consume_budget_and_keep_checkpoints(catalogue, monkeypatch):
    for index in range(3):
        _insert(catalogue, str(index), 2012 + index)
    calls = []

    def fetch(_session, url, **_kwargs):
        calls.append(url)
        if url.endswith("/1"):
            raise requests.Timeout("temporary")
        return {"message": {"DOI": url.removeprefix("https://api.crossref.org/works/"),
                            "title": ["An Opaque Name"]}}

    monkeypatch.setattr(conferences, "_get_json", fetch)
    assert conferences.enrich_candidate_abstracts(catalogue, limit=2) == 0
    assert len(calls) == 2
    assert [r[0] for r in catalogue.execute(
        "SELECT abstract_status FROM conference_papers ORDER BY year"
    )] == ["unavailable", "error", None]
    assert conferences.enrich_candidate_abstracts(catalogue, limit=2) == 0
    assert len(calls) == 3
    assert conferences.enrich_candidate_abstracts(catalogue, limit=2) == 0
    assert len(calls) == 3
    catalogue.execute("UPDATE conference_papers SET abstract_checked_at='2000-01-01' WHERE paper_id='1'")
    assert conferences.enrich_candidate_abstracts(catalogue, limit=1) == 0
    assert len(calls) == 4


@pytest.mark.parametrize("message,expected", [
    ({"DOI": "10.1234/a", "title": ["An Opaque Name"]}, "found"),
    ({"DOI": "10.1234/wrong", "title": ["An Opaque Name"]}, "mismatch"),
    ({"DOI": "10.1234/a", "title": ["A different paper"]}, "mismatch"),
    ({"DOI": "10.1234/a", "title": ["An"], "subtitle": ["Opaque Name"]}, "found"),
    ({"DOI": "10.1234/a", "title": "An Opaque Name"}, "mismatch"),
])
def test_abstract_identity_including_publisher_subtitles(catalogue, monkeypatch, message, expected):
    _insert(catalogue, "a")
    monkeypatch.setattr(conferences, "_get_json", lambda *a, **kw: {
        "message": {**message, "abstract": "A KVM study."}})
    conferences.enrich_candidate_abstracts(catalogue, limit=1)
    row = catalogue.execute("SELECT abstract,abstract_status FROM conference_papers").fetchone()
    assert row["abstract_status"] == expected
    assert bool(row["abstract"]) == (expected == "found")


def test_discovery_and_candidates_respect_year_and_venue(catalogue, monkeypatch):
    _insert(catalogue, "a", 2010, "osdi", "KVM study")
    _insert(catalogue, "b", 2012, "osdi", "KVM study")
    _insert(catalogue, "c", 2012, "atc", "KVM study")
    calls = []
    monkeypatch.setattr(conferences, "_get_json", lambda _s, url, **kw: calls.append(url) or {})
    conferences.enrich_candidate_abstracts(catalogue, start_year=2011, end_year=2013, venues=iter(["osdi"]))
    assert len(calls) == 1 and calls[0].endswith("/b")
    assert [r["paper_id"] for r in conferences.candidate_rows(catalogue, 2011, 2013, ["osdi"])] == ["b"]
    with pytest.raises(ValueError):
        conferences.enrich_candidate_abstracts(catalogue, limit=0)


def test_coverage_does_not_treat_curated_import_as_full_catalogue(catalogue):
    _insert(catalogue, "a")
    row = conferences.catalogue_coverage(catalogue, 2012, 2012, ["osdi"])[0]
    assert row["catalogue_status"] == "not_checked"
    assert row["stored_titles"] == row["unreviewed"] == row["without_abstract"] == 1
    assert row["keyword_candidates"] == 0


def test_historical_backfill_has_original_sources_and_honest_publication_types():
    content = conferences.load_content()
    by_id = {paper["id"]: paper for paper in content["papers"]}
    ids = {"atc05-qemu", "ols07-kvm", "osr08-virtio", "osdi12-dune",
           "asplos13-unikernels", "nsdi14-clickos", "atc14-osv", "eurosys20-seuss",
           "asplos20-catalyzer", "atc23-lambda-loading"}
    for key in ids:
        paper = by_id[key]
        assert paper["year"] < 2026
        assert paper["publication_status"] == "published"
        assert paper["evidence_level"] in {"abstract", "project"}
        assert paper["evidence_source_url"].startswith("https://")
    assert by_id["osr08-virtio"]["venue"] == "osr"
    assert by_id["atc05-qemu"]["institutions"] == []
    assert {"osr", "ols"} <= {v["key"] for v in content["paper_venues"]}
    assert {2005, 2007, 2008} <= set(content["years"])
    # Historical records lacking provenance stay unknown, with no fake review date.
    raw = json.loads(conferences.CONTENT_PATH.read_text())
    for paper in raw["papers"]:
        if "evidence_level" not in paper:
            assert by_id[paper["id"]]["evidence_level"] == "unspecified"
            assert by_id[paper["id"]].get("evidence_verified_at") == paper.get("evidence_verified_at")


@pytest.mark.parametrize("title", [
    "My VM is Lighter (and Safer) than your Container",
    "ClickOS and the Art of Network Function Virtualization",
    "SEUSS: Unikernel Snapshots for Serverless Computing",
    "CEMU: Enabling Full-System Emulation of Computational Storage",
])
def test_historical_candidate_vocabulary(title):
    assert conferences.is_candidate_title(title)
