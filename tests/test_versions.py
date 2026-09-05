"""Release identity, official dates and offline snapshot regressions."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from virt_report import versions, scheduler
from virt_report.config import Config, Storage
from virt_report.render import render


def record(project="qemu", version="11.1.0", released_on="2026-08-11"):
    return versions._record(project, version, released_on, "https://www.qemu.org/download/")


def test_version_identity_matches_each_projects_release_scheme():
    today = date(2026, 9, 5)
    assert versions._valid(record(), today)
    assert versions._valid(record("kvm", "7.2"), today)
    assert not versions._valid(record("kvm", "7.2.0"), today)
    assert not versions._valid(record("qemu", "11.1"), today)
    assert not versions._valid(dict(record(version="11.1.1"), kind="feature"), today)


@pytest.mark.parametrize("project,version,family", [
    ("libvirt", "0.0.1", "0.0.1"), ("libvirt", "1.2.21", "1.2.21"),
    ("libvirt", "1.2.21.3", "1.2.21"), ("libvirt", "2.0.1", "2.0.0"),
    ("kvm", "2.6.20", "2.6.20"), ("kvm", "2.6.20.21", "2.6.20"),
    ("kvm", "2.6.19", None), ("kvm", "3.0.101", "3.0"),
    ("kvm", "7.3-rc1", None), ("qemu", "1.0", "1.0"),
    ("qemu", "1.0.1", "1.0"), ("qemu", "0.9.1", "0.9.1"),
    ("qemu", "0.10.1", "0.10.0"), ("qemu", "11.1.0-rc0", None),
])
def test_historical_release_families(project, version, family):
    assert versions.release_family(project, version) == family
    assert versions._valid(record(project, version), date(2026, 9, 5)) == (family is not None)


def test_libvirt_ignores_unreleased_and_preserves_published_date():
    html = '''<div class="section" id="next"><h1>v12.8.0 (unreleased)</h1></div>
    <div class="section" id="v12-7-0-2026-09-01"><h1>v12.7.0 (2026-09-01)</h1>
      <ul><li><p><strong>New features</strong></p><ul>
        <li><p>qemu: improve migration</p><p>Longer description.</p></li>
      </ul></li></ul></div>'''
    rows = versions.parse_libvirt(html)
    assert len(rows) == 1
    assert rows[0]["released_on"] == "2026-09-01"
    assert rows[0]["highlights"] == ["qemu: improve migration"]
    assert rows[0]["source_url"].endswith("#v12-7-0-2026-09-01")


def test_qemu_archive_deduplicates_links_and_reads_announcement_date():
    link = '<a href="../../../2026/08/11/qemu-11-1-0/">QEMU version 11.1.0 released</a>'
    rows = versions.parse_qemu_archive(link * 2 + '<a href="/future">QEMU 12.0-rc1</a>')
    assert len(rows) == 1
    assert rows[0]["source_url"] == "https://www.qemu.org/2026/08/11/qemu-11-1-0/"
    assert rows[0]["released_on"] == "2026-08-11"


def test_kernel_uses_annotated_tag_date_not_commit_or_archive_date():
    html = '''<div class="Metadata-row"><dt>tagger</dt><dd>
      <div class="Metadata-descriptionCell">Linus Torvalds</div>
      <div class="Metadata-descriptionCell">Sun Nov 17 14:15:08 2024 -0800</div>
    </dd></div><pre class="MetadataMessage">Linux 6.12
    -----BEGIN PGP SIGNATURE-----</pre>'''
    result = versions.parse_kernel_tag(html, "6.12")
    assert result["released_on"] == "2024-11-17"
    assert result["released_at"] == "2024-11-17T22:15:08+00:00"
    with pytest.raises(ValueError):
        versions.parse_kernel_tag(html.replace("Linux 6.12", "Fix KVM crash"), "6.12")


def test_kvm_bullets_keep_architecture_context():
    notes = versions.parse_kvm_notes('''<pre class="MetadataMessage">ARM:
* Add Arm migration feature
  with extra details

x86:
* Fix nested guest state

-----BEGIN PGP SIGNATURE-----
ignore</pre>''')
    assert notes == ["ARM: Add Arm migration feature with extra details", "x86: Fix nested guest state"]


def test_kvm_notes_resolve_historical_tag_names():
    refs = {"kvm-7.2-2": {}, "kvm-7.2-1": {}, "tags/kvm-6.8-1": {},
            "for-linus-6.0": {}, "kvm-7.20-1": {}}
    assert versions._kvm_note_ref(refs, "7.2") == "kvm-7.2-1"
    assert versions._kvm_note_ref(refs, "6.8") == "tags/kvm-6.8-1"
    assert versions._kvm_note_ref(refs, "6.0") == "for-linus-6.0"
    assert versions._kvm_note_ref(refs, "6.1") is None


def test_source_cache_revalidates_and_reuses_immutable_pages(tmp_path, monkeypatch):
    requests = []

    class Response:
        status_code = 200
        text = "official release"
        content = text.encode()
        headers = {"ETag": '"release-1"', "Last-Modified": "Fri, 04 Sep 2026 12:00:00 GMT"}

    def get(url, **kwargs):
        requests.append((url, kwargs))
        response = Response()
        if len(requests) > 1:
            response.status_code = 304
            response.text = ""
        return response

    monkeypatch.setattr(versions, "http_get", get)
    cache = versions.SourceCache(tmp_path)
    url = "https://www.qemu.org/release"
    assert cache.get(url) == "official release"
    assert cache.get(url) == "official release"
    assert requests[1][1]["headers"]["If-None-Match"] == '"release-1"'
    assert cache.get(url, immutable=True) == "official release"
    assert len(requests) == 2


def test_shared_historical_archive_is_downloaded_once(tmp_path, monkeypatch):
    requests = []

    class Response:
        status_code = 200
        text = "official release archive"
        content = text.encode()
        headers = {}

    def get(url, **_kwargs):
        requests.append(url)
        return Response()

    monkeypatch.setattr(versions, "http_get", get)
    cache = versions.SourceCache(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: cache.get("https://www.qemu.org/archive/", immutable=True), range(8)))
    assert results == [Response.text] * 8
    assert len(requests) == 1


def test_public_snapshot_includes_maintenance_but_excludes_runtime_status(tmp_path, monkeypatch):
    config = Config(storage=Storage(db_path=tmp_path / "data" / "test.db"))
    content = tmp_path / "content"
    monkeypatch.setattr(versions, "CONTENT_DIR", content)
    versions._write_json(config.db_path.parent / "versions.json", {
        "checked_at": "2026-09-05T00:00:00+00:00",
        "sources": {"qemu": {"ok": False, "error": "private runtime detail"}},
        "releases": [record(), record(version="11.1.1")],
    })
    path = versions.export_public_snapshot(config, today=date(2026, 9, 5))
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    assert {row["version"] for row in snapshot["releases"]} == {"11.1.0", "11.1.1"}
    assert "private runtime detail" not in path.read_text(encoding="utf-8")
    assert "project_label" not in snapshot["releases"][0]
    # A fresh installation can render the baseline without runtime data.
    (config.db_path.parent / "versions.json").unlink()
    assert len(versions.load_content(config, today=date(2026, 9, 5))["releases"]) == 2


def test_kernel_history_keeps_feature_and_latest_branch_patch_not_rc():
    refs = {name: {} for name in (
        "v2.6.19", "v2.6.20", "v2.6.20.21", "v3.0", "v3.0.9", "v3.0.101",
        "v7.2", "v7.2.2", "v7.2.3", "v7.3-rc1", "v7.3-rc2", "v7.4.2",
    )}
    assert versions.kernel_versions(refs) == [
        "7.2.3", "7.2", "3.0.101", "3.0", "2.6.20.21", "2.6.20",
    ]


def test_kernel_failed_tag_does_not_hide_other_successes():

    class Cache:
        def get(self, url, **kwargs):
            if url == versions.KERNEL_TAGS:
                return json.dumps({"v6.12": {}, "v6.12.1": {}, "v7.3-rc1": {}})
            raise ValueError("source unavailable")

    rows = versions.fetch_project("kvm", Cache(), 2022, date(2026, 9, 5), {
        "6.12": record("kvm", "6.12", "2024-11-17"),
    })
    assert [row["version"] for row in rows] == ["6.12"]
    assert rows.failed_versions == ["6.12.1"]


def test_refresh_preserves_failed_sources_and_removes_future_and_rc(tmp_path, monkeypatch):
    config = Config(storage=Storage(db_path=tmp_path / "data" / "test.db"))
    content = tmp_path / "content"
    content.mkdir()
    monkeypatch.setattr(versions, "CONTENT_DIR", content)
    versions._write_json(content / "versions.json", {"releases": [record()]})

    def fetch(project, *_args):
        if project == "qemu":
            raise RuntimeError("source temporarily unavailable")
        return [record(project, "7.2" if project == "kvm" else "12.7.0", "2026-09-01"),
                record(project, "13.0.0", "2027-01-01"),
                record(project, "13.0-rc1", "2026-09-01")]

    monkeypatch.setattr(versions, "fetch_project", fetch)
    snapshot = versions.refresh(config, today=date(2026, 9, 5))
    assert len(snapshot["releases"]) == 3
    assert snapshot["sources"]["qemu"]["ok"] is False
    assert snapshot["sources"]["libvirt"]["ok"] is True
    assert any(row["version"] == "11.1.0" for row in snapshot["releases"])
    monkeypatch.setattr(versions, "http_get", lambda *_a, **_k: pytest.fail("Web reader requested network"))
    assert len(versions.load_content(config, today=date(2026, 9, 5))["releases"]) == 3
    # A corrupt runtime snapshot still leaves the bundled public record usable.
    (config.db_path.parent / "versions.json").write_text("{broken", encoding="utf-8")
    assert len(versions.load_content(config, today=date(2026, 9, 5))["releases"]) == 1


def test_version_page_preserves_features_and_excludes_maintenance_from_html():
    releases = [dict(record(version=f"11.{i}.0"), year="2026",
                     project_label="QEMU", notes=[], note_source_url="https://www.qemu.org/download/")
                for i in range(12)]
    releases.append(dict(record(version="11.1.1"), year="2026", project_label="QEMU"))
    releases.append(dict(record("libvirt", "1.2.21", "2015-10-01"), year="2015", project_label="Libvirt"))
    releases.append(dict(record("libvirt", "1.2.21.3", "2015-11-01"), year="2015", project_label="Libvirt"))
    releases.append(dict(record("kvm", "7.2.3"), year="2026", project_label="KVM"))
    releases[0].update(notes=["改进迁移状态检查。"], commits=120, contributors=17)
    snapshot_before = json.dumps(releases, sort_keys=True)
    page = render.render_versions_html(Config(), {
        "releases": releases, "projects": versions.PROJECTS, "years": ["2026"],
        "default_year": "2027", "checked_at": "", "sources": {},
    })
    document = versions.Document(page).root
    entries = [node for node in document.find("li") if "data-version-item" in node.attrs]
    assert len(entries) == 13
    visible = [node for node in entries if "hidden" not in node.attrs]
    assert len(visible) == 12
    assert all(node.attrs["data-project"] == "qemu" for node in visible)
    assert "12 个功能版本" in page
    assert '<option value="qemu" selected>QEMU</option>' in page
    assert 'data-default-project="qemu"' in page
    groups = [node for node in document.find("section") if "data-version-group" in node.attrs]
    assert [node.attrs["id"] for node in groups if "hidden" not in node.attrs] == ["year-2026"]
    for group in groups:
        disclosure = group.find("details")[0]
        assert "open" in disclosure.attrs and "data-version-disclosure" in disclosure.attrs
        heading = disclosure.find("summary")[0].find("h2")[0]
        assert heading.attrs["id"] == group.attrs["aria-labelledby"]
    assert "12 个版本" in page
    assert "1 / 2" in page
    assert '<option value="">全部年份</option>' in page
    for patch_id in ("qemu-11.1.1", "libvirt-1.2.21.3", "kvm-7.2.3"):
        assert f'id="release-{patch_id}"' not in page
    assert 'data-view="compact"' in page
    assert 'data-version-controls hidden' in page
    assert page.index('id="year-2026"') < page.index('id="year-2015"')
    nav = next(node for node in document.find("nav") if node.attrs.get("class") == "main-nav")
    assert not any("versions.html" in node.attrs.get("href", "") for node in nav.find("a"))
    assert 'id="release-libvirt-1.2.21"' in page  # Historical micro number is a feature.
    assert "version-maintenance" not in page and "维护更新" not in page
    assert "改进迁移状态检查。" in page and "120 次提交" in page and "17 位贡献者" in page
    assert "官方完整版本记录" in page
    assert 'href="https://gitlab.com/qemu-project/qemu/-/tags"' in page
    assert 'href="https://gitlab.com/libvirt/libvirt/-/tags?search=%5Ev"' in page
    assert 'href="https://kernel.googlesource.com/pub/scm/linux/kernel/git/stable/linux/+refs"' in page
    assert json.dumps(releases, sort_keys=True) == snapshot_before


def test_version_default_filter_with_no_qemu_releases():
    page = render.render_versions_html(Config(), {
        "releases": [dict(record("libvirt", "1.2.21", "2015-10-01"),
                          year="2015", project_label="Libvirt")],
        "projects": versions.PROJECTS, "checked_at": "",
    })
    document = versions.Document(page).root
    entries = [node for node in document.find("li") if "data-version-item" in node.attrs]
    assert len(entries) == 1 and "hidden" in entries[0].attrs
    assert "0 个功能版本" in page
    assert '<option value="qemu" selected>QEMU</option>' in page
    empty = next(node for node in document.find("p") if "data-version-empty" in node.attrs)
    assert "hidden" not in empty.attrs


def test_report_toc_follows_hero_before_body():
    daily = render.render_report_html(Config(), {
        "period": "daily", "period_key": "2026-09-04", "label": "2026-09-04",
        "stats": {}, "overview": [{"project": "QEMU", "summary": "Migration"}],
        "watchlist": [], "sections": [],
    })
    assert daily.index('class="report-hero"') < daily.index('data-report-toc') < daily.index('class="post"')
    assert daily.count("data-report-toc") == 1


def test_gitlab_dates_skip_lightweight_and_resigned_tags():
    tag = {"name": "v11.1.0", "created_at": "2026-08-11T21:00:00Z",
           "commit": {"committed_date": "2026-08-11T20:30:00+00:00"}}
    assert versions.parse_gitlab_tag("qemu", tag)["released_on"] == "2026-08-11"
    assert versions.parse_gitlab_tag("qemu", dict(tag, created_at=None)) is None
    assert versions.parse_gitlab_tag("qemu", dict(tag, created_at="2026-09-05T00:00:00Z")) is None
    assert versions.parse_gitlab_tag("qemu", dict(tag, name="v11.1.0-rc1")) is None


def test_gitlab_tag_pagination_uses_every_page():
    requested = []

    class Cache:
        def get(self, url):
            requested.append(url)
            if "page=2" in url:
                return json.dumps([{"name": "v1.2.21.1"}])
            return json.dumps([{"name": "v2.0.0"}] * 100)

    result = versions.fetch_gitlab_tags("libvirt", Cache())
    assert len(requested) == 2
    assert [tag["name"] for tag in result] == ["v2.0.0", "v1.2.21.1"]


@pytest.mark.parametrize("message", ["Linux v4.19", "This is the 4.19 release", "4.19"])
def test_kernel_tag_historical_messages(message):
    html = '<div class="Metadata-row"><dt>tagger</dt><div class="Metadata-descriptionCell">Mon Oct 22 07:47:45 2018 +0100</div></div>'
    assert versions.parse_kernel_tag(html + f'<pre class="MetadataMessage">{message}</pre>', "4.19")["released_on"] == "2018-10-22"


def test_qemu_announcement_requires_original_formal_release():
    base = "https://lists.nongnu.org/archive/html/qemu-devel/2010-10/"
    titles = ["[Qemu-devel] [ANNOUNCE] Release 0.13.0 of QEMU",
              "Re: [Qemu-devel] [ANNOUNCE] Release 0.13.0 of QEMU",
              "QEMU 0.13.0-rc1 released", "QEMU 0.13.0 compilation error"]
    text = "".join(f'<a href="msg{i}.html">{title}</a>' for i, title in enumerate(titles))
    assert versions.qemu_announcement_links(text, base, "0.13.0") == [base + "msg0.html"]
    mail = '<table><tr><td><b>Date</b>:</td><td>Mon, 18 Oct 2010 09:04:42 -0500</td></tr></table>'
    assert versions.parse_mail_date(mail) == "2010-10-18"


def test_missing_qemu_announcement_is_not_retried_daily(tmp_path):
    class Cache:
        directory = tmp_path
        requests = 0

        def get(self, *_args, **_kwargs):
            self.requests += 1
            return "<html>No matching announcement</html>"

    cache = Cache()
    tag = {"name": "v0.12.0", "commit": {"committed_date": "2009-12-19T08:26:29-06:00"}}
    assert versions.fetch_qemu_announcement(tag, cache) is None
    assert versions.fetch_qemu_announcement(tag, cache) is None
    assert cache.requests == 3


def test_failed_qemu_archive_lookup_is_retried_not_cached_as_absent(tmp_path):
    class Cache:
        directory = tmp_path
        requests = 0

        def get(self, *_args, **_kwargs):
            self.requests += 1
            raise RuntimeError("temporary outage")

    cache = Cache()
    tag = {"name": "v0.12.0", "commit": {"committed_date": "2009-12-19T08:26:29-06:00"}}
    assert versions.fetch_qemu_announcement(tag, cache) is None
    assert versions.fetch_qemu_announcement(tag, cache) is None
    assert cache.requests == 6


def test_partial_refresh_exposes_failures_without_losing_new_rows(tmp_path, monkeypatch):
    config = Config(storage=Storage(db_path=tmp_path / "test.db"))
    monkeypatch.setattr(versions, "CONTENT_DIR", tmp_path / "content")

    def fetch(project, *_args):
        if project == "kvm":
            result = versions.ReleaseBatch()
            result.append(record("kvm", "7.2"))
            result.failed_versions.append("7.2.3")
            return result
        return [record(project)]

    monkeypatch.setattr(versions, "fetch_project", fetch)
    result = versions.refresh(config, today=date(2026, 9, 5))
    assert result["sources"]["kvm"]["ok"] is False
    assert result["sources"]["kvm"]["failed_versions"] == ["7.2.3"]
    assert any(row["project"] == "kvm" for row in result["releases"])


def test_version_refresh_is_a_separate_daily_scheduler_job():
    now = datetime(2026, 9, 5, 3, 35, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert scheduler.scheduled_commands(Config(), now) == [("versions", ["versions-refresh"])]
