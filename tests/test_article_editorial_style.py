"""Guard specific editorial regressions without banning technical negations."""
import re

import pytest

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render


@pytest.mark.parametrize("article", mechanisms.articles(), ids=lambda article: article["slug"])
def test_articles_omit_redundant_scope_commentary(article):
    page = render.render_mechanisms_html(Config(), article["slug"])
    # These are discarded editorial asides, not restrictions on words such as
    # "不" or on useful qualifications about ownership, completion and errors.
    for phrase in (
        "这里先看普通 Linux 线程",
        "不引入虚拟化层",
        "本篇不把这些虚拟化策略混入",
        "不把示意路径冒充本机测量结果",
        "这里不另加一套重复演示",
        "不混入本次 HLT 演示",
        "网络不是“应用请求读一个包",
        "不是“所有任务运行同样久”，而是",
    ):
        assert phrase not in page


def test_wait_queue_explanation_sets_state_before_rechecking_condition():
    page = render.render_mechanisms_html(Config(), "linux-execution-scheduling")
    path = page.split('<section id="path"', 1)[1].split("</section>", 1)[0]
    text = re.sub(r"<[^>]*>", "", path)
    assert "先登记等待者并设置等待状态，再检查完成条件" in text
    assert "条件仍未满足才调用调度器" in text
    assert "prepare_to_wait_event()" in text and "finish_wait()" in text
    assert "完成侧须先更新条件再唤醒" in text
    assert "include/linux/wait.h#L300-L329" in path


def test_napi_example_keeps_full_and_zero_budget_rules():
    page = render.render_mechanisms_html(Config(), "linux-network-io")
    section = page.split('<section id="scaling"', 1)[1].split("</section>", 1)[0]
    text = re.sub(r"<[^>]*>", "", section)
    assert "剩余 36 个" in text
    assert "budget - 1" in text
    assert "预算为 0" in text and "禁止调用 napi_complete_done()" in text


def test_block_article_links_to_shared_virtqueue_explanation():
    page = render.render_mechanisms_html(Config(), "qemu-virtio-blk")
    assert 'href="packed-virtqueue.html#split-layout"' in page


def test_linux_further_reading_keeps_historical_evaluation_context():
    page = render.render_mechanisms_html(Config(), "linux-execution-scheduling")
    sources = page.split('<section id="sources"', 1)[1]
    assert 'href="https://doi.org/10.1145/2901318.2901326"' in sources
    assert 'href="https://people.ece.ubc.ca/sasha/papers/eurosys16-final29.pdf"' in sources
    assert 'href="../conference-papers.html#paper-eurosys16-linux-scheduler"' in sources
    for context in ("EuroSys 2016", "CFS", "64 核", "NUMA", "Linux 3.17", "4.3", "EEVDF"):
        assert context in sources
    memory = render.render_mechanisms_html(Config(), "linux-memory")
    assert 'href="../conference-papers.html#paper-osdi16-ingens"' in memory
