import json
import sqlite3

from virt_report import conferences, db
from virt_report.config import Config
from virt_report.render import render as html_render


def _public_content(papers):
    return {
        "version": 1,
        "venues": [{
            "key": "osdi", "name": "OSDI", "kind": "学术会议",
            "url": "https://example.com/osdi",
        }],
        "papers": papers,
        "analysis": {"years": []},
    }


def _paper(**extra):
    result = {
        "id": "osdi25-example", "year": 2025, "venue": "osdi",
        "title": "A KVM Hypervisor", "url": "https://example.com/paper",
        "introduction": "简介", "commentary": "点评",
        "relation": "直接关联", "topics": ["KVM"],
    }
    result.update(extra)
    return result


def test_conference_affiliation_schema_migrates_existing_database(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE conference_papers (
            paper_id TEXT PRIMARY KEY, venue TEXT NOT NULL, year INTEGER NOT NULL,
            title TEXT NOT NULL, authors_json TEXT NOT NULL DEFAULT '[]',
            abstract TEXT, doi TEXT, official_url TEXT, source_url TEXT,
            fetched_at TEXT NOT NULL, raw_json TEXT
        )
    """)
    conn.execute("""
        INSERT INTO conference_papers
        (paper_id,venue,year,title,fetched_at) VALUES ('p','osdi',2025,'T','now')
    """)
    conn.commit()
    conn.close()

    migrated = db.connect(path)
    columns = {row[1] for row in migrated.execute(
        "PRAGMA table_info(conference_papers)"
    )}
    assert {
        "affiliations_json", "institutions_json", "affiliation_source",
        "affiliation_source_url", "affiliation_verified_at",
    } <= columns
    assert migrated.execute(
        "SELECT affiliations_json FROM conference_papers WHERE paper_id='p'"
    ).fetchone()[0] == "[]"
    migrated.close()


def test_crossref_affiliation_enrichment_and_public_sync(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "catalogue.db")
    conn.execute("""
        INSERT INTO conference_papers (
            paper_id,venue,year,title,authors_json,doi,official_url,source_url,
            fetched_at,raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        conferences.paper_id("osdi", 2025, "A KVM Hypervisor"),
        "osdi", 2025, "A KVM Hypervisor", "[]", "10.1/test",
        "https://example.com/paper", "https://dblp.org/rec/example", "now", "{}",
    ))
    conn.commit()
    conferences.import_editor_reviews(conn, _public_content([_paper()]))
    calls = []

    def fake_get_json(_session, url, *, params):
        calls.append((url, params))
        return {"message": {
            "DOI": "10.1/test",
            "author": [
                {"given": "Alice", "family": "Chen",
                 "affiliation": [{"name": "Example University"}]},
                {"given": "Bob", "family": "Li",
                 "affiliation": [{"name": "Example Labs"},
                                 {"name": "Example University"}]},
            ],
        }}

    monkeypatch.setattr(conferences, "_request_session", object)
    monkeypatch.setattr(conferences, "_get_json", fake_get_json)
    monkeypatch.setattr(conferences.time, "sleep", lambda _delay: None)
    result = conferences.enrich_affiliations(conn)
    assert result == {
        "checked": 1, "authors_updated": 1, "affiliations_updated": 1,
        "without_affiliations": 0, "errors": 0,
    }
    assert calls == [(conferences.CROSSREF_API.format("10.1/test"),
                      {"mailto": conferences.CROSSREF_MAILTO})]
    row = conn.execute("""
        SELECT authors_json,affiliations_json,affiliation_source,
               affiliation_source_url,affiliation_verified_at
        FROM conference_papers
    """).fetchone()
    assert json.loads(row["authors_json"]) == ["Alice Chen", "Bob Li"]
    mappings = json.loads(row["affiliations_json"])
    assert mappings[1]["institutions"] == ["Example Labs", "Example University"]
    assert row["affiliation_source"] == "crossref"
    assert row["affiliation_source_url"].endswith("10.1/test")
    assert row["affiliation_verified_at"].endswith("Z")
    assert json.loads(conn.execute(
        "SELECT institutions_json FROM conference_papers"
    ).fetchone()[0]) == ["Example University", "Example Labs"]

    content_path = tmp_path / "conferences.json"
    content_path.write_text(
        json.dumps(_public_content([_paper()]), ensure_ascii=False),
        encoding="utf-8",
    )
    assert conferences.sync_public_metadata(conn, content_path) == 1
    public = json.loads(content_path.read_text(encoding="utf-8"))["papers"][0]
    assert public["authors"] == ["Alice Chen", "Bob Li"]
    assert public["institutions"] == ["Example University", "Example Labs"]
    assert public["author_affiliations"] == mappings
    assert public["affiliation_source"] == "crossref"

    # A checked DOI is skipped unless the operator explicitly requests refresh.
    assert conferences.enrich_affiliations(conn)["checked"] == 0
    assert len(calls) == 1
    conn.close()


def test_crossref_doi_discovery_requires_exact_unambiguous_title(
        tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "discovery.db")
    conferences.import_editor_reviews(conn, _public_content([_paper()]))

    def fake_get_json(_session, url, *, params):
        assert url == conferences.CROSSREF_WORKS_API
        assert params["filter"].startswith("from-pub-date:2025")
        return {"message": {"items": [
            {"DOI": "10.1/fuzzy", "title": ["A Related Hypervisor"],
             "published": {"date-parts": [[2025]]}},
            {"DOI": "10.1/exact", "title": ["A KVM Hypervisor."],
             "published": {"date-parts": [[2025, 7]]}},
        ]}}

    monkeypatch.setattr(conferences, "_request_session", object)
    monkeypatch.setattr(conferences, "_get_json", fake_get_json)
    monkeypatch.setattr(conferences.time, "sleep", lambda _delay: None)
    result = conferences.discover_curated_dois(conn)
    assert result == {
        "checked": 1, "found": 1, "not_found": 0,
        "ambiguous": 0, "errors": 0,
    }
    assert conn.execute(
        "SELECT doi FROM conference_papers"
    ).fetchone()[0] == "10.1/exact"
    conn.close()


def test_usenix_html_metadata_is_primary_and_does_not_store_email(
        tmp_path, monkeypatch):
    paper = _paper(url="https://www.usenix.org/conference/osdi25/presentation/test")
    conn = db.connect(tmp_path / "usenix.db")
    conferences.import_editor_reviews(conn, _public_content([paper]))
    page = """<html><head>
      <meta name="citation_title" content="A KVM Hypervisor" />
      <meta name="citation_author" content="Alice Chen" />
      <meta name="citation_author_institution" content="Example University" />
      <meta name="citation_author_email" content="private@example.edu" />
      <meta name="citation_author" content="Bob Li" />
      <meta name="citation_author_institution" content="Example Labs" />
    </head></html>"""
    monkeypatch.setattr(conferences, "_request_session", object)
    monkeypatch.setattr(conferences, "_get_html", lambda _session, _url: page)
    monkeypatch.setattr(conferences.time, "sleep", lambda _delay: None)
    result = conferences.enrich_usenix_affiliations(conn)
    assert result == {
        "eligible": 1, "checked": 1, "updated": 1,
        "without_affiliations": 0, "title_mismatch": 0, "errors": 0,
    }
    row = conn.execute("""
        SELECT authors_json,affiliations_json,affiliation_source,
               affiliation_source_url,affiliation_verified_at
        FROM conference_papers
    """).fetchone()
    assert json.loads(row["authors_json"]) == ["Alice Chen", "Bob Li"]
    assert json.loads(row["affiliations_json"])[0] == {
        "name": "Alice Chen", "institutions": ["Example University"],
    }
    assert row["affiliation_source"] == "usenix"
    assert row["affiliation_source_url"] == paper["url"]
    assert row["affiliation_verified_at"].endswith("Z")
    assert "private@example.edu" not in "".join(str(value) for value in row)
    conn.close()


def test_public_affiliations_render_compactly_and_missing_values_stay_hidden(
        tmp_path, monkeypatch):
    path = tmp_path / "conferences.json"
    papers = [
        _paper(
            authors=["A", "B", "C", "D", "E", "F"],
            author_affiliations=[
                {"name": "A", "institutions": ["University One"]},
                {"name": "B", "institutions": ["Company Two"]},
            ],
            institutions=[
                "SCS, Peking University, China", "Peking University",
                "Company Two", "University Three", "Lab Four", "Center Five",
            ],
            affiliation_source="crossref",
            affiliation_source_url="https://api.crossref.org/works/10.1/test",
            affiliation_verified_at="2026-08-04T00:00:00Z",
        ),
        _paper(id="osdi24-missing", year=2024, title="Another KVM Paper"),
    ]
    path.write_text(
        json.dumps(_public_content(papers), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(conferences, "CONTENT_PATH", path)
    monkeypatch.setattr(
        conferences, "INSTITUTION_OVERRIDES_PATH", tmp_path / "missing.json"
    )
    conferences.load_content.cache_clear()
    content = conferences.load_content()
    page = html_render.render_conference_papers_html(Config(), content)
    assert "<strong>作者</strong>" not in page
    assert ("Peking University · Company Two · University Three · Lab Four"
            in page)
    assert "等 5 家单位" in page
    assert page.count("Peking University") == 2
    assert page.count("<strong>单位</strong>") == 1
    assert "查看全部单位" in page and "Center Five" in page
    assert "单位元数据来源：Crossref；核验时间：2026-08-04T00:00:00Z" in page
    conferences.load_content.cache_clear()
