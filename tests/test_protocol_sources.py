"""Protocol feeds do not duplicate evidence or retroactively alter coverage."""
from datetime import datetime, timezone

from virt_report import coverage, db
from virt_report.config import Config, MailingListSource, Sources, load_config
from virt_report.summarize.report import _deduplicate_crossposts, _project_of, _sanitize
from virt_report import search


def test_configured_protocol_sources_have_bounded_optional_coverage():
    sources = {item.name: item for item in load_config().sources.mailing_lists}
    for name in ("virtio-dev", "virtio-comment", "vhost-user"):
        assert not sources[name].required
        assert sources[name].active_from == "2026-11-05"
        assert sources[name].url == f"https://lore.kernel.org/{name}/git/0.git"
    assert _project_of({"project": "virtio-comment"}) == ("virtio", "Virtio")
    assert search._project_fields("virtio-dev") == ("virtio", "Virtio")
    assert search._project_fields("vhost-user") == ("qemu", "QEMU")


def test_template_fallback_keeps_virtio_overview():
    origins = [{"project": "virtio-dev", "url": "https://lore.kernel.org/virtio-dev/a/"},
               {"project": "virtio-comment", "url": "https://lore.kernel.org/virtio-comment/a/"}]
    overview, sections = _sanitize([{"project": "Virtio", "summary": "规范评审动态"}],
                                  [{"key": "virtio", "name": "Virtio",
                                    "items": [{"title": "Example", "crossposts": origins}]}], None)
    assert any(item["project"] == "Virtio" for item in overview)
    assert sections[0]["items"][0]["crossposts"] == origins


def test_crossposts_preserve_origins_without_collapsing_distinct_reviews():
    def card(project, mids):
        return {"project": project, "source": "ml", "url": f"https://lore.kernel.org/{project}/a/",
                "message_ids": mids, "ref": "old", "rank": 100}
    original = [card("qemu-devel", ["a", "b"]), card("virtio-dev", ["b", "a"]),
                card("virtio-comment", ["a", "b", "new-review"]), card("virtio-dev", ["v2"])]
    result = _deduplicate_crossposts(original)
    assert len(result) == 3
    assert len(result[0]["crossposts"]) == 2
    assert result[1]["project"] == "virtio-comment"
    assert [card["ref"] for card in result] == ["T001", "T002", "T003"]
    assert original[0]["message_ids"] == ["a", "b"]


def test_new_source_does_not_create_historical_gaps_or_block_old_sources(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    config = Config(sources=Sources(mailing_lists=[
        MailingListSource("old", "mbox", "https://example.org/"),
        MailingListSource("new", "lore_git", "https://example.org/git/0.git", False, "2026-11-05"),
    ]))
    coverage.ensure_schema(conn)
    coverage.record_scan(conn, key="test", source="ml", project="old", start="2026-11-01T00:00:00Z",
                         end="2026-11-07T00:00:00Z", started_at="2026-11-07T00:00:00Z", status="complete", page_limit=8)
    historical = coverage.report_coverage(conn, config, "2026-11-01", "2026-11-02")
    assert historical["status"] == "complete"
    current = coverage.report_coverage(conn, config, "2026-11-01", datetime(2026, 11, 6, tzinfo=timezone.utc))
    assert current["status"] == "partial" and not current["wait_recommended"]
    assert current["sources"][1]["gaps"][0]["start"] == "2026-11-05T00:00:00Z"
    conn.close()
