from pathlib import Path

import pytest

from virt_report import db, search
from virt_report.config import Config
from virt_report.processing import threads
from virt_report.render import render


@pytest.fixture
def search_db(tmp_path: Path):
    conn = db.connect(tmp_path / "search.db")
    yield conn
    conn.close()


def _insert(conn, native_id: str, subject: str, url: str, *, project: str,
            kind: str = "patch", created: str = "2026-07-10T00:00:00Z") -> None:
    db.upsert_item(conn, {
        "source": "ml", "project": project, "native_id": native_id,
        "message_id": native_id, "in_reply_to": None, "thread_root": native_id,
        "author": "developer@example.com", "subject": subject, "kind": kind,
        "created_at": created, "updated_at": created, "activity_at": created,
        "url": url, "body_excerpt": "evidence", "raw_json": {},
    })


def _save_review(conn, period: str, key: str, url: str, *, summary: str) -> None:
    db.save_report(conn, period, key, {
        "period": period, "period_key": key, "fallback": False,
        "sections": [{
            "key": "qemu", "name": "QEMU", "items": [{
                "url": url,
                "title": "迁移：缩短热迁移停机时间",
                "original_title": "[PATCH v3] migration: reduce live migration downtime",
                "summary": summary,
                "impact": "降低业务切换期间的停顿。",
                "category": "feature", "architectures": ["x86"],
                "source": "QEMU 邮件列表", "time": "2026-07-10",
            }],
        }],
    }, "Asia/Shanghai", item_count=1, model="test")


def test_search_index_deduplicates_reports_and_searches_both_languages(search_db):
    reviewed_url = "https://example.com/migration"
    _insert(
        search_db, "migration", "[PATCH v3] migration: reduce live migration downtime",
        reviewed_url, project="qemu-devel",
    )
    _insert(
        search_db, "nested", "[RFC] KVM: add nested virtualization support",
        "https://example.com/nested", project="kvm",
    )
    threads.rebuild_threads(search_db)
    _save_review(search_db, "daily", "2026-07-10", reviewed_url,
                 summary="日报点评：优化迁移切换流程。")
    _save_review(search_db, "weekly", "2026-W28", reviewed_url,
                 summary="周报点评：该方案进一步收敛了停机窗口。")

    status = search.refresh_index(search_db)
    assert status["document_count"] == 1
    assert status["curated_count"] == 1
    assert search_db.execute("SELECT COUNT(*) FROM search_documents_fts").fetchone()[0] == 1

    chinese = search.search(search_db, "热迁移")
    assert chinese["total"] == 1
    assert chinese["expansions"] == ["live migration", "migration"]
    item = chinese["items"][0]
    assert item["display_title"] == "迁移：缩短热迁移停机时间"
    assert item["summary"] == "周报点评：该方案进一步收敛了停机窗口。"
    assert len(item["report_refs"]) == 2

    assert search.search(search_db, "nested virtualization")["total"] == 0
    assert search.search(search_db, "嵌套虚拟化")["total"] == 0
    assert search_db.execute(
        "SELECT COUNT(*) FROM search_documents WHERE thread_key LIKE '%nested%'"
    ).fetchone()[0] == 0


def test_search_filters_short_chinese_and_escapes_queries(search_db):
    _insert(
        search_db, "migration", "[PATCH] migration: add x86 fast path",
        "https://example.com/migration", project="qemu-devel",
        created="2026-07-10T00:00:00Z",
    )
    _insert(
        search_db, "arm", "[PATCH] KVM: arm64 migration fix",
        "https://example.com/arm", project="kvm",
        created="2026-06-01T00:00:00Z",
    )
    _insert(
        search_db, "riscv", "[PATCH] KVM: RISC-V migration optimization",
        "https://example.com/riscv", project="kvm",
        created="2026-07-15T00:00:00Z",
    )
    threads.rebuild_threads(search_db)
    db.save_report(search_db, "monthly", "2026-07", {
        "period": "monthly", "period_key": "2026-07", "fallback": False,
        "sections": [{"key": "qemu", "items": [{
            "url": "https://example.com/migration", "title": "x86 迁移快速路径",
            "original_title": "[PATCH] migration: add x86 fast path",
            "summary": "增加 x86 迁移快速路径。", "impact": "降低迁移开销。",
            "category": "feature", "architectures": ["x86"],
            "source": "QEMU 邮件列表", "time": "2026-07-10",
        }]}, {"key": "kvm", "items": [{
            "url": "https://example.com/arm", "title": "Arm 迁移缺陷修复",
            "original_title": "[PATCH] KVM: arm64 migration fix",
            "summary": "修复 Arm 迁移问题。", "impact": "避免迁移失败。",
            "category": "bug", "architectures": ["Arm"],
            "source": "KVM 邮件列表", "time": "2026-06-01",
        }, {
            "url": "https://example.com/riscv", "title": "RISC-V 迁移优化",
            "original_title": "[PATCH] KVM: RISC-V migration optimization",
            "summary": "优化 RISC-V 虚机迁移。", "impact": "降低迁移耗时。",
            "category": "feature", "architectures": ["RISC-V"],
            "source": "KVM 邮件列表", "time": "2026-07-15",
        }]}],
    }, "Asia/Shanghai", item_count=3, model="test")
    search.refresh_index(search_db)

    result = search.search(
        search_db, "迁移", project="qemu", category_key="feature",
        architecture_key="x86",
    )
    assert result["total"] == 1
    assert result["items"][0]["project"] == "qemu"
    assert result["items"][0]["architectures"] == ["x86"]
    other = search.search(search_db, "迁移", architecture_key="other")
    assert other["total"] == 1
    assert other["items"][0]["architectures"] == ["RISC-V"]
    latest = search.search(search_db, "迁移", sort="latest")
    oldest = search.search(search_db, "迁移", sort="oldest")
    assert [item["date"] for item in latest["items"]] == [
        "2026-07-15", "2026-07-10", "2026-06-01",
    ]
    assert [item["date"] for item in oldest["items"]] == [
        "2026-06-01", "2026-07-10", "2026-07-15",
    ]
    assert search.search(search_db, "迁移", sort="invalid")["sort"] == "relevance"
    assert search.search(search_db, "%_' OR *")["error"] == ""
    assert search.search(search_db, "K")["error"] == "请至少输入 2 个字符。"


def test_search_page_renders_results_filters_and_safe_highlights(search_db):
    url = "https://example.com/vfio"
    _insert(
        search_db, "vfio", "[PATCH] VFIO: add <script>alert(1)</script> support",
        url, project="qemu-devel",
    )
    threads.rebuild_threads(search_db)
    _save_review(search_db, "daily", "2026-07-10", url,
                 summary="VFIO 热迁移点评。")
    search.refresh_index(search_db)
    result = search.search(search_db, "VFIO")
    html = render.render_search_html(Config(), result)

    assert "<title>搜索 - virt-report</title>" in html
    assert ('href="about.html" class="">关于</a><a href="search.html" '
            'class="nav-search-icon on" aria-label="搜索"') in html
    assert 'class="mobile-search-icon on" aria-label="搜索"' in html
    assert '<mark>VFIO</mark>' in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "查看日报点评" in html
    assert "全部社区议题" not in html
    assert 'name="architecture"' in html
    assert '>其他架构</option>' in html
    assert 'name="sort"' in html
    assert '>最新优先</option>' in html and '>最早优先</option>' in html
    assert 'name="date_from"' not in html and 'name="date_to"' not in html
    assert "requestSubmit()" in html
    assert 'data-search-clear aria-label="清除搜索"' in html
    assert "searchQuery.addEventListener('input'" in html
    assert "searchClear.addEventListener('click'" in html
    assert "window.location.assign(searchForm.action)" in html
    assert 'aria-label="搜索建议"' not in html

    landing = render.render_search_html(Config(), search.search(search_db, ""))
    assert "试试搜索" not in landing
    assert 'class="search-examples" role="group" aria-label="搜索建议"' in landing
    assert (landing.index('class="search-query-row"') <
            landing.index('aria-label="搜索建议"') <
            landing.index('class="search-filters"'))
    assert "点评与原标题" not in landing
    assert "中英文术语" not in landing
    assert "搜索边界" not in landing
    assert 'placeholder="搜索热迁移、VFIO、virtio…"' in landing
    assert "search.html?q=热迁移" in landing
    assert "search.html?q=VFIO" in landing
    assert "search.html?q=virtio" in landing
    assert "search.html?q=CVE" not in landing
    assert "search.html?q=guest_memfd" not in landing
    assert "search.html?q=热升级" not in landing
    assert "search.html?q=嵌套虚拟化" not in landing
    assert 'data-search-clear aria-label="清除搜索" title="清除搜索" hidden' in landing

    about = render.render_about_html(Config())
    assert "社区议题搜索" in about
    assert '<time datetime="2026-08-20">2026 年 8 月 20 日</time>' in about
