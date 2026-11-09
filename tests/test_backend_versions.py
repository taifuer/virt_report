"""Backend software releases and the shared version navigation."""
import json
from datetime import date

import pytest

from virt_report import versions
from virt_report.config import Config, Storage, load_config
from virt_report.render.render import render_versions_html


TODAY = date(2026, 11, 9)
DPDK_INDEX = '''<table>
<tr><td><a href="https://fast.dpdk.org/rel/dpdk-26.07.tar.xz">DPDK 26.07.0</a></td>
<td><a href="https://doc.dpdk.org/guides-26.07/rel_notes/release_26_07.html">2026 July 22</a></td></tr>
<tr><td><a href="https://fast.dpdk.org/rel/dpdk-25.11.4.tar.xz">DPDK 25.11.4 (LTS)</a></td>
<td><a href="https://doc.dpdk.org/guides-25.11/rel_notes/release_25_11.html">2026 October 14</a></td></tr>
<tr><td><a href="/rc">DPDK 26.11-rc1</a></td><td>2026 October 30</td></tr></table>'''
TAG = '''<h1>v25.11</h1><p>Maintainer tagged this <relative-time datetime="2025-11-30T16:22:52Z">30 Nov</relative-time></p><pre>dpdk-25.11</pre>'''


@pytest.mark.parametrize("project,version,feature", [
    ("dpdk", "2.2.0", "2.2.0"), ("dpdk", "26.07.0", "26.07.0"),
    ("dpdk", "25.11.4", "25.11.0"), ("spdk", "26.09", "26.09"),
    ("spdk", "25.05.2", "25.05"), ("spdk", "26.09-rc1", None),
    ("spdk", "26.99", None), ("vhost-user", "1.4", None),
])
def test_distinct_release_numbering(project, version, feature):
    assert versions.release_family(project, version) == feature


def test_dpdk_index_and_annotated_tag_dates_are_not_maintenance_dates():
    rows = versions.parse_dpdk_releases(DPDK_INDEX)
    assert [row["version"] for row in rows] == ["26.07.0", "25.11.4"]
    assert [row["kind"] for row in rows] == ["feature", "maintenance"]
    assert rows[0]["released_on"] == "2026-07-22"
    base = versions.parse_dpdk_tag(TAG, "25.11.0")
    assert base["released_on"] == "2025-11-30"
    assert base["date_source"] == "release_tag"
    assert base["kind"] == "feature"
    for html in (TAG.replace("dpdk-25.11", "dpdk-24.11"),
                 TAG.replace("tagged this", "committed"),
                 TAG.replace("2025-11-30T16:22:52Z", "2025-11-30T16:22:52"),
                 TAG + '<relative-time datetime="2026-10-14T00:00:00Z"/>'):
        with pytest.raises(ValueError):
            versions.parse_dpdk_tag(html, "25.11.0")


def test_dpdk_base_lookup_is_bounded_and_preserves_partial_results():
    calls = []

    class Cache:
        def get(self, url, **kwargs):
            calls.append(url)
            if url == versions.DPDK_DOWNLOAD:
                return DPDK_INDEX
            assert url == "https://github.com/DPDK/dpdk/releases/tag/v25.11"
            assert kwargs["immutable"]
            return TAG

    rows = versions.fetch_dpdk_releases(Cache(), {}, 2020)
    assert len(rows) == 3 and rows.failed_versions == []
    calls.clear()
    assert len(versions.fetch_dpdk_releases(Cache(), {"25.11.0": rows[-1]}, 2020)) == 2
    assert calls == [versions.DPDK_DOWNLOAD]

    class Failure(Cache):
        def get(self, url, **kwargs):
            if url != versions.DPDK_DOWNLOAD:
                raise ValueError("offline")
            return super().get(url, **kwargs)

    partial = versions.fetch_dpdk_releases(Failure(), {}, 2020)
    assert len(partial) == 2 and partial.failed_versions == ["25.11.0"]


def spdk_post(version="26.09", path="release", visible_date="Sep 24, 2026"):
    return f'''<div class="blog-section"><a href="../{path}/2026/09/24/v26.09_release/">
    <h2>SPDK v{version}: NVMe-oF Key Value</h2></a>
    <div class="blog-author">{visible_date} • Maintainer</div></div>'''


def test_spdk_uses_official_release_posts_and_checks_both_dates():
    rows = versions.parse_spdk_releases(spdk_post() + spdk_post("26.09-rc1") + spdk_post(path="performance"))
    assert len(rows) == 1
    assert rows[0]["released_on"] == "2026-09-24"
    assert rows[0]["highlights"] == ["NVMe-oF Key Value"]
    assert rows[0]["source_url"].startswith("https://spdk.io/release/")
    with pytest.raises(ValueError):
        versions.parse_spdk_releases(spdk_post(visible_date="Sep 25, 2026"))
    with pytest.raises(ValueError):
        versions.parse_spdk_releases("<h1>2027 roadmap</h1>")


def test_version_navigation_places_virtio_before_backend_frameworks(tmp_path):
    config = Config(storage=Storage(tmp_path / "db"))
    content = versions.load_content(config, today=TODAY)
    # An older caller's reference metadata must not recreate the removed entry.
    content["protocols"] = [{"id": "vhost-user", "name": "vhost-user"}]
    page = render_versions_html(config, content)
    document = versions.Document(page).root
    selector = next(node for node in document.find("select") if node.attrs.get("id") == "version-project")
    assert [node.attrs["value"] for node in selector.find("option")] == [
        "", "qemu", "kvm", "libvirt", "virtio", "dpdk", "spdk"]
    latest = [node for node in document.find("a") if "data-version-jump" in node.attrs]
    assert [node.find("strong")[0].text() for node in latest] == [
        "QEMU", "KVM", "Libvirt", "Virtio", "DPDK", "SPDK"]
    assert latest[3].attrs["href"] == "#release-virtio-1.4-cs01"
    assert "CS01 · 协议规范" in latest[3].text()
    assert 'id="protocol-vhost-user"' not in page
    assert "data-version-protocol" not in page
    assert all(row["project"] != "vhost-user" for row in content["releases"])


def test_retired_version_reference_does_not_affect_data_or_mail_collection(tmp_path, monkeypatch):
    config = Config(storage=Storage(tmp_path / "data" / "db"))
    content_dir = tmp_path / "content"
    monkeypatch.setattr(versions, "CONTENT_DIR", content_dir)
    snapshot_path = config.db_path.parent / "versions.json"
    legacy = {"protocols": [{"id": "vhost-user", "message_version": "0x01"}],
              "sources": {"vhost-user": {"ok": False}, "qemu": {"ok": True}}}
    versions._write_json(snapshot_path, legacy)
    content = versions.load_content(config, today=TODAY)
    assert "protocols" not in content
    assert content["sources"] == {"qemu": {"ok": True}}
    exported = json.loads(versions.export_public_snapshot(config, today=TODAY).read_text())
    assert "protocols" not in exported
    assert json.loads(snapshot_path.read_text()) == legacy
    assert any(source.name == "vhost-user" for source in load_config().sources.mailing_lists)


def test_earliest_listed_draft_is_verified_but_not_claimed_as_first(tmp_path):
    config = Config(storage=Storage(tmp_path / "db"))
    content = versions.load_content(config, today=TODAY)
    spec = next(row for row in content["specifications"] if row["version"] == "0.8.7")
    assert spec["stage"] == "Legacy" and spec["released_on"] == "2010-04-20"
    assert versions._valid_specification(spec, TODAY)
    assert not versions._valid_specification({**spec, "released_on": "2012-06-26"}, TODAY)
    assert "不能据此认定它是首版" in render_versions_html(config, content)


def test_recent_backend_releases_have_reviewed_notes_and_real_sources(tmp_path):
    content = versions.load_content(Config(storage=Storage(tmp_path / "db")), today=TODAY)
    records = {(row["project"], row["version"]): row for row in content["releases"]}
    for key in (("dpdk", "26.07.0"), ("spdk", "26.09"), ("spdk", "26.05")):
        assert len(records[key]["notes"]) == 3
        assert records[key]["kind"] == "feature"
        assert records[key]["source_url"].startswith(("https://doc.dpdk.org/", "https://spdk.io/release/"))
