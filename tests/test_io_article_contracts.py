"""Source-linked editorial contracts for RX, packed queues and vDPA."""
from pathlib import Path
import re

import pytest

from virt_report import mechanisms


TEMPLATES = Path(__file__).resolve().parents[1] / "virt_report/render/templates"


@pytest.mark.parametrize("slug,template", [
    ("network-io", "_network_io_article.html"),
    ("packed-virtqueue", "_packed_virtqueue_article.html"),
    ("vhost-vdpa", "_vdpa_article.html"),
])
def test_io_articles_have_complete_outline_and_fixed_source_baselines(slug, template):
    article = next(item for item in mechanisms.articles() if item["slug"] == slug)
    source = (TEMPLATES / template).read_text()
    outline = re.findall(r'<section id="([^"]+)"[^>]*>\s*<h2>([^<]+)</h2>', source)
    assert outline == [(item["id"], item["title"]) for item in article["sections"]]
    assert article["topic"] == "device-virtualization"
    assert len(outline) >= 8
    assert all(re.fullmatch("[a-f0-9]{40}", item["commit"])
               for item in article["baselines"])
    assert "{% include '_mechanism_baselines.html' %}" in source
    assert "{{ diagram(" in source
    assert not re.search(r"不是[^。]{0,100}而是", source)


def test_rx_distinguishes_capacity_actual_length_and_replenishment():
    source = (TEMPLATES / "_network_io_article.html").read_text()
    for term in ("1530 B", "72 B", "128 B", "try_fill_recv", "virtqueue_napi_complete",
                 "budget = 0", "MRG_RXBUF", "DMA 映射", "旧数据页可能仍被 skb 引用"):
        assert term in source


def test_packed_explains_id_chains_wrap_and_non_generalized_demo():
    source = (TEMPLATES / "_packed_virtqueue_article.html").read_text()
    for term in ("最后一个描述符", "没有 next 字段", "完成记录的位置与原请求的位置可以不同",
                 "不需要把槽位清零", "不要求是 2 的幂", "不模拟 DMA 时延",
                 'data-packed-action="publish"', 'data-packed-action="reclaim"'):
        assert term in source


def test_vdpa_explains_dma_mappings_and_migration_limits():
    source = (TEMPLATES / "_vdpa_article.html").read_text()
    for term in ("VHOST_IOTLB_UPDATE", "VHOST_IOTLB_INVALIDATE", "Shadow Virtqueue",
                 "直接模式由设备访问队列与报文", "逐包解析和复制",
                 "迁移阻止原因", "软件实现", "IOVA 与 GPA 未必相等",
                 "其他环节的复制取决于设备、驱动及工作模式"):
        assert term in source
