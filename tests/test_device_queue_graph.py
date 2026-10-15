"""Keep the packet walkthrough's queue geometry tied to its four-slot model."""
from pathlib import Path
import re

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "virt_report" / "render" / "templates"


def test_split_queue_graph_renders_each_slot_and_descriptor():
    env = Environment(loader=FileSystemLoader(TEMPLATES))
    page = env.get_template("_device_tx_queue_graph.html").render()
    for kind in ("avail", "used"):
        assert re.findall(rf'data-tx-graphic-{kind}-slot="(\d+)"', page) == [
            "0", "1", "2", "3",
        ]
    assert re.findall(r'data-tx-graphic-desc="(\d+)"', page) == ["0", "1", "2", "3"]
    for field in ("addr", "len", "flags", "next"):
        assert page.count(f'data-field="{field}"') == 4
    assert "0x1000" in page and "0x2000" in page
    assert 'data-tx-graphic-path="publish"' in page
    assert 'data-tx-graphic-path="complete"' in page
    assert "数据不存放在环槽中" in page
    assert "kick / call eventfd 只承载通知" in page
    assert 'aria-labelledby="tx-queue-graphic-title tx-queue-graphic-desc"' in page


def test_queue_graph_is_visible_by_default_and_has_local_step_controls():
    env = Environment(loader=FileSystemLoader(TEMPLATES))
    page = env.get_template("_device_tx_lab.html").render()
    assert '<option value="virtio" selected>' in page
    for control in ("queue-next", "queue-back", "queue-reset", "graphic-step"):
        assert f"data-tx-{control}" in page
    assert 'class="device-tx-queue-graphic"' in page
    css = (ROOT / "virt_report" / "render" / "assets" / "device-tx.css").read_text()
    assert ".device-tx-queue-graphic{max-width:100%;min-width:0;overflow:auto;" in css


def test_dpdk_article_distinguishes_copy_and_completion_ownership():
    article = (TEMPLATES / "_dpdk_article.html").read_text()
    assert "1b3bf1128d9bda5595861814792f74b8f57160c8" in article
    for symbol in ("rte_vhost_dequeue_burst", "rte_vhost_enqueue_burst",
                   "desc_to_mbuf", "rte_eth_tx_burst", "data_off", "nb_segs"):
        assert symbol in article
    assert "后 12 个仍归应用" in article
    assert "enqueue 没有接管这个 mbuf 的生命周期" in article
    assert "本例不属于端到端零拷贝" in article
    assert "spdk-vhost-user-blk.html" in article
