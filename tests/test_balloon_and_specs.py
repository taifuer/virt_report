"""Balloon accounting and protocol versions have different completion semantics."""
from datetime import date

from virt_report import mechanisms, versions
from virt_report.config import Config, Storage
from virt_report.execution_demos import get_demo
from virt_report.render import render


def final_values(scene):
    values = {field["key"]: field["initial"] for group in scene["structures"] for field in group["fields"]}
    for step in scene["steps"]:
        values.update(step["changes"])
    return values


def test_balloon_targets_are_page_counts_not_qmp_bytes_or_rss():
    scenes = {scene["id"]: scene for scene in get_demo("virtio-balloon")["scenarios"]}
    inflated = final_values(scenes["inflate"])
    assert inflated["configured"] == "8 GiB"
    assert inflated["numPages"] == inflated["actual"] == "524288"
    assert int(inflated["actual"]) * 4096 == 2 * 1024**3
    blocked = final_values(scenes["inhibited"])
    assert blocked["actual"] == inflated["actual"]
    assert "未释放" in blocked["discard"]
    assert "需实测" in blocked["hostRss"]
    for name in ("deflate", "report"):
        values = final_values(scenes[name])
        assert values["held"] == values["actual"] == values["numPages"] == "0"
    assert "WRITE" in final_values(scenes["report"])["pages"]
    assert "PFN[256]" in inflated["pages"]


def test_balloon_releases_pages_only_after_queue_completion():
    for scene in get_demo("virtio-balloon")["scenarios"]:
        ids = [step["id"] for step in scene["steps"]]
        assert ids.index("publish") < ids.index("complete")
        if scene["id"] in {"deflate", "report"}:
            release = next(i for i, step in enumerate(scene["steps"]) if "release" in step["links"])
            assert release > ids.index("complete")


def test_specification_stage_comes_from_cover_and_keeps_drafts_distinct():
    html = "<h1>Virtual I/O Device (VIRTIO) Version 1.3</h1><h2>Committee Specification Draft 01</h2><p>06 October 2023</p>"
    draft = versions.parse_virtio_specification(html, "1.3")
    assert draft["stage"] == "CSD01"
    assert "草案" in draft["status"]
    assert versions._valid_specification(draft, date(2026, 11, 5))
    assert not versions._valid_specification({**draft, "status": "委员会规范"}, date(2026, 11, 5))
    assert not versions._valid_specification({**draft, "released_on": "2027-01-01"}, date(2026, 11, 5))


def test_specification_cover_ignores_deeply_nested_document_body():
    html = ("<h1>Virtual I/O Device (VIRTIO) Version 1.2</h1>"
            "<h2>Committee Specification 01</h2><p>01 July 2022</p>"
            + "<div>" * 1500 + "Body references an older version" + "</div>" * 1500)
    spec = versions.parse_virtio_specification(html, "1.2")
    assert spec["stage"] == "CS01"
    assert spec["released_on"] == "2022-07-01"


def test_offline_specification_records_and_article_baselines(tmp_path):
    content = versions.load_content(Config(storage=Storage(db_path=tmp_path / "db")))
    rows = {row["version"]: row for row in content["specifications"]}
    assert rows["1.4"]["stage"] == "CS01"
    assert rows["1.3"]["stage"] == "CSD01"
    assert rows["0.9.5"]["stage"] == "Legacy"
    assert rows["0.9.5"]["released_on"] == "2012-05-07"
    article = mechanisms.get_article("virtio-device-initialization")
    assert article["specifications"][0]["label"] == "VIRTIO 1.2 CS01"
    assert article["baselines"][0]["version"] == "10.2.0"


def test_device_reading_groups_cover_all_articles_once():
    topic = mechanisms.get_topic("device-virtualization")
    grouped = [article["slug"] for cluster in topic["article_clusters"] for article in cluster["articles"]]
    assert len(grouped) == len(set(grouped)) == len(topic["detail_articles"])
    assert set(grouped) == {article["slug"] for article in topic["detail_articles"]}


def test_protocol_versions_share_project_browser_without_becoming_software_releases(tmp_path):
    config = Config(storage=Storage(db_path=tmp_path / "db"))
    content = versions.load_content(config)
    projects = dict(content["projects"])
    page = render.render_versions_html(config, content)
    document = versions.Document(page).root
    selector = next(node for node in document.find("select") if node.attrs.get("id") == "version-project")
    assert [node.attrs["value"] for node in selector.find("option")] == ["", "qemu", "kvm", "libvirt", "virtio"]
    records = [node for node in document.find("li") if node.attrs.get("data-project") == "virtio"]
    assert len(records) == len(content["specifications"])
    assert all(node.attrs["data-kind"] == "specification" for node in records)
    assert all("hidden" in node.attrs for node in records)  # Default QEMU filter stays consistent.
    assert 'id="release-virtio-1.3-csd01"' in page and 'data-draft="true"' in page
    assert 'href="#release-virtio-1.4-cs01" data-version-jump' in page
    assert 'id="virtio-specifications"' in page  # Previously shared anchors still work.
    assert 'class="version-specifications"' not in page
    assert content["projects"] == projects  # Protocol navigation is a presentation projection.
    assert not any(row["project"] == "virtio" for row in content["releases"])


def test_specification_details_have_reviewed_notes_and_inline_document_links(tmp_path):
    config = Config(storage=Storage(db_path=tmp_path / "db"))
    content = versions.load_content(config, today=date(2026, 11, 7))
    page = render.render_versions_html(config, content)
    document = versions.Document(page).root
    records = {node.attrs["id"]: node for node in document.find("li")
               if node.attrs.get("data-kind") == "specification"}
    for spec in content["specifications"]:
        assert 2 <= len(spec["notes"]) <= 3
        record = records[f'release-virtio-{spec["version"]}-{spec["stage"].lower()}']
        actions = next(node for node in record.find("div")
                       if node.attrs.get("class") == "version-actions")
        assert [node.attrs["href"] for node in actions.find("a")] == [
            "https://github.com/oasis-tcs/virtio-admin/blob/master/charter.html"
            if spec["stage"] == "Legacy" else spec["source_url"],
            spec["source_url"].replace(".html", ".pdf")]
        detail = next(node for node in record.find("div")
                      if node.attrs.get("class") == "version-detail")
        assert len(detail.find("li")) == len(spec["notes"])
        assert all(note in detail.text() for note in spec["notes"])


def test_specification_notes_survive_metadata_refresh_but_do_not_cross_stages(tmp_path, monkeypatch):
    config = Config(storage=Storage(db_path=tmp_path / "data" / "db"))
    today = date(2026, 11, 7)
    original = versions.load_content(config, today=today)["specifications"][0]
    metadata = {key: value for key, value in original.items() if key != "notes"}
    content_dir = tmp_path / "content"
    versions._write_json(content_dir / "virtio_specification_notes.json", {
        f'{metadata["version"]}:{metadata["stage"]}': original["notes"]})
    monkeypatch.setattr(versions, "CONTENT_DIR", content_dir)
    next_stage = {**metadata, "stage": "CS02", "released_on": "2026-11-06",
                  "source_url": metadata["source_url"].replace("cs01", "cs02")}
    versions._write_json(config.db_path.parent / "versions.json", {
        "specifications": [{**metadata, "notes": ["过时的本地说明"]}, next_stage]})
    rows = {row["stage"]: row for row in versions.load_content(config, today=today)["specifications"]}
    assert rows["CS01"]["notes"] == original["notes"]
    assert rows["CS02"]["notes"] == []  # A new document stage needs its own review.
    exported = versions._read_json(versions.export_public_snapshot(config, today=today), {})
    assert all("notes" not in row for row in exported["specifications"])
    assert versions.load_content(config, today=today)["specifications"][1]["notes"] == original["notes"]


def test_classic_specification_keeps_verified_origin_date_and_draft_status(tmp_path):
    today = date(2026, 11, 8)
    config = Config(storage=Storage(db_path=tmp_path / "db"))
    content = versions.load_content(config, today=today)
    legacy = next(row for row in content["specifications"] if row["stage"] == "Legacy")
    assert versions._valid_specification(legacy, today)
    assert not versions._valid_specification(legacy, date(2012, 5, 6))
    for changes in ({"version": "0.9.4"}, {"stage": "CS01"}, {"status": "委员会规范"},
                    {"released_on": "2013-01-01"}, {"source_url": "https://example.com/spec.pdf"}):
        assert not versions._valid_specification({**legacy, **changes}, today)
    page = render.render_versions_html(config, {**content, "specifications": [legacy]})
    document = versions.Document(page).root
    entry = next(node for node in document.find("li")
                 if node.attrs.get("id") == "release-virtio-0.9.5-legacy")
    assert "早期规范草案" in entry.text()
    assert any(node.attrs.get("data-draft") == "true" for node in entry.find("p"))
    assert not any(node.attrs.get("href") == "#release-virtio-0.9.5-legacy"
                   and "data-version-jump" in node.attrs for node in document.find("a"))
    assert "VIRTIO_F_VERSION_1" in page and "virtio-win 0.1.xxx" in page


def test_refresh_retains_legacy_without_requesting_nonexistent_oasis_version(tmp_path, monkeypatch):
    config = Config(storage=Storage(db_path=tmp_path / "data" / "db"))
    today = date(2026, 11, 8)
    content = versions.load_content(config, today=today)
    specs = [row for row in content["specifications"] if row["version"] in {"0.9.5", "1.2"}]
    content_dir = tmp_path / "content"
    versions._write_json(content_dir / "virtio_specifications.json", {"releases": specs})
    monkeypatch.setattr(versions, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(versions, "fetch_project", lambda project, *_args: [
        next(row for row in content["releases"] if row["project"] == project)])
    requested = []

    def get(_cache, url):
        requested.append(url)
        assert url == "https://docs.oasis-open.org/virtio/virtio/v1.2/virtio-v1.2.html"
        return ("<h1>Virtual I/O Device (VIRTIO) Version 1.2</h1>"
                "<h2>Committee Specification 01</h2><p>01 July 2022</p>")

    monkeypatch.setattr(versions.SourceCache, "get", get)
    result = versions.refresh(config, today=today)
    assert len(requested) == 1
    assert result["sources"]["virtio"]["ok"] is True
    assert result["sources"]["virtio"]["failed_versions"] == []
    assert {row["version"] for row in result["specifications"]} == {"0.9.5", "1.2"}
    assert all("notes" not in row for row in result["specifications"])
    reloaded = versions.load_content(config, today=today)
    assert next(row for row in reloaded["specifications"] if row["stage"] == "Legacy")["released_on"] == "2012-05-07"
