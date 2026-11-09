"""The common queue article covers both layouts without breaking old links."""
from virt_report import mechanisms, versions
from virt_report.config import Config
from virt_report.render.render import render_mechanisms_html


def test_queue_article_combines_split_and_packed_at_the_existing_url():
    article = mechanisms.get_article("packed-virtqueue")
    assert article["title"] == "Virtqueue：Split 与 Packed 的请求与完成"
    page = render_mechanisms_html(Config(), "packed-virtqueue")
    root = versions.Document(page).root
    ids = {node.attrs.get("id") for node in root.find("section")}
    assert {"split-layout", "split-lifecycle", "layout", "wrap", "interactive", "chains", "events"} <= ids
    assert "used.id=2" in page and "avail.idx: 0 → 1" in page
    assert "16 位 idx 按 65536 回绕" in page
    assert 'data-queue-format="split"' in page and 'data-queue-format="packed"' in page
    assert "实际设备在初始化时选定一种格式" in page
    assert "没有启用 IN_ORDER" in page or "不启用 IN_ORDER" in page
    assert "used.len 表示设备写入的长度" in page
    assert "RING_PACKED" in page


def test_protocol_history_keeps_draft_status_and_source_baselines_separate():
    page = render_mechanisms_html(Config(), "virtio-device-initialization")
    for label in ("2007", "0.8.7", "0.9.5", "1.0", "1.1", "1.2", "1.3 CSD01", "1.4 CS01"):
        assert label in page
    assert "不能据此称它为第一版" in page
    assert "Modern 可以使用 Split" in page
    assert "virtio-win 0.1.xxx" in page
    assert "目前缺乏可核验的全行业部署比例" in page
    assert "10.2.0" in page and "6.18" in page
    assert 'href="packed-virtqueue.html#interactive"' in page
