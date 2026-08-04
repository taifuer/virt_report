"""Report statistics rendering and backwards-compatibility tests."""
from __future__ import annotations

from virt_report import db
from virt_report.config import Config
from virt_report.processing import threads
from virt_report.render import render as html_render
from virt_report.summarize import report


def _report_content(by_project: dict[str, int]) -> dict:
    return {
        "period": "daily",
        "period_key": "2026-08-03",
        "label": "2026-08-03",
        "headline": "",
        "fallback": False,
        "model": "test",
        "timezone": "Asia/Shanghai",
        "stats": {
            "total_threads": 5,
            "total_items": sum(by_project.values()),
            "ml_patches": 3,
            "ml_rfc": 0,
            "gl_issues_opened": 2,
            "gl_mrs_merged": 0,
            "by_project": by_project,
        },
        "overview": [{"project": "QEMU", "summary": "测试概览"}],
        "watchlist": [],
        "sections": [],
    }


def test_legacy_project_stats_render_in_product_and_source_order():
    content = _report_content({
        "libvirt": 5,
        "libvir-list": 4,
        "kvm": 3,
        "qemu": 2,
        "qemu-devel": 1,
    })

    page = html_render.render_report_html(Config(), content)
    stats_html = page.split('id="stats"', 1)[1].split("</section>", 1)[0]
    labels = [
        '<span class="project-name">QEMU</span><span '
        'class="source-type mailing_list">邮件列表</span>',
        '<span class="project-name">QEMU</span><span '
        'class="source-type gitlab">GitLab</span>',
        '<span class="project-name">KVM</span><span '
        'class="source-type mailing_list">邮件列表</span>',
        '<span class="project-name">Libvirt</span><span '
        'class="source-type mailing_list">邮件列表</span>',
        '<span class="project-name">Libvirt</span><span '
        'class="source-type gitlab">GitLab</span>',
    ]
    positions = [stats_html.index(label) for label in labels]

    assert positions == sorted(positions)
    assert "qemu-devel" not in stats_html
    assert "libvir-list" not in stats_html
    # Rendering an old saved report must not mutate its in-memory JSON.
    assert "by_project_source" not in content["stats"]


def test_new_project_source_rows_are_canonical_and_ordered():
    stats = {
        "by_project_source": [
            {
                "project": "libvirt", "project_label": "internal-name",
                "source": "gitlab", "source_label": "internal-source",
                "count": 2,
            },
            {
                "project": "qemu", "project_label": "internal-name",
                "source": "mailing_list", "source_label": "internal-source",
                "count": 4,
            },
            {
                "project": "kvm", "project_label": "internal-name",
                "source": "ml", "source_label": "internal-source",
                "count": 3,
            },
        ]
    }

    rows = report.project_source_rows(stats)

    assert [(row["project_label"], row["source_label"], row["count"])
            for row in rows] == [
        ("QEMU", "邮件列表", 4),
        ("KVM", "邮件列表", 3),
        ("Libvirt", "GitLab", 2),
    ]


def test_stats_save_structured_project_source_breakdown(tmp_path):
    conn = db.connect(tmp_path / "stats.db")
    try:
        identities = [
            ("gitlab", "libvirt", "issue"),
            ("ml", "libvir-list", "patch"),
            ("ml", "kvm", "patch"),
            ("gitlab", "qemu", "issue"),
            ("ml", "qemu-devel", "patch"),
        ]
        for index, (source, project, kind) in enumerate(identities):
            created = f"2026-08-03T0{index}:00:00Z"
            db.upsert_item(conn, {
                "source": source,
                "project": project,
                "native_id": f"item-{index}",
                "message_id": f"item-{index}" if source == "ml" else None,
                "in_reply_to": None,
                "thread_root": f"item-{index}",
                "author": "tester",
                "subject": f"change {index}",
                "kind": kind,
                "created_at": created,
                "updated_at": created,
                "url": f"https://example.test/{index}",
                "body_excerpt": "evidence",
                "raw_json": {"type": "issue"} if source == "gitlab" else {},
            })
        threads.rebuild_threads(conn)

        stats = report._stats(
            conn, "2026-08-03T00:00:00Z", "2026-08-04T00:00:00Z"
        )

        assert [(row["project_label"], row["source_label"], row["count"])
                for row in stats["by_project_source"]] == [
            ("QEMU", "邮件列表", 1),
            ("QEMU", "GitLab", 1),
            ("KVM", "邮件列表", 1),
            ("Libvirt", "邮件列表", 1),
            ("Libvirt", "GitLab", 1),
        ]
        assert stats["by_project"] == {
            "libvirt": 1,
            "libvir-list": 1,
            "kvm": 1,
            "qemu": 1,
            "qemu-devel": 1,
        }
    finally:
        conn.close()
