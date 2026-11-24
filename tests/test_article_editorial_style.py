"""Guard specific editorial regressions without banning technical negations."""
import re
from pathlib import Path

import pytest

from virt_report import conferences, kvm_forum, mechanisms
from virt_report.config import Config
from virt_report.render import render


# Target stock editorial caveats without banning useful technical conditions.
STOCK_CAVEAT = re.compile(
    r"(?:不能|无法|不足以)(?:据此|由此|直接|单独)?(?:证明|说明|推断|断言|概括)"
    r"|(?:不能|无法)(?:据此|由此)(?:认定|声称)"
    r"|(?:并不代表|而非证明|不作为.{0,12}结论|分析仅供参考)"
    r"|关闭(?:状态)?(?:不代表|不自动等于).{0,4}(?:修复|实现)"
    r"|不能把.{0,42}(?:理解为|当作|看成|套到|套用|画成)"
)


@pytest.mark.parametrize("article", mechanisms.articles(), ids=lambda article: article["slug"])
def test_articles_omit_redundant_scope_commentary(article):
    page = render.render_mechanisms_html(Config(), article["slug"])
    assert not STOCK_CAVEAT.search(page)
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


def test_reviews_omit_stock_caveats_and_keep_the_basis_for_comparisons():
    config = Config()
    content = conferences.load_content()
    editions, analysis = kvm_forum.load_content()
    pages = {
        "conferences": render.render_conferences_html(config, content),
        "academic": render.render_academic_conferences_html(config, content),
        "papers": render.render_conference_papers_html(config, content),
        "forum": render.render_kvm_forum_html(config, editions, analysis),
    }
    pages.update({edition["year"]: render.render_kvm_forum_review_html(config, edition)
                  for edition in editions if edition.get("review")})
    for name, page in pages.items():
        assert not STOCK_CAVEAT.search(page), name

    # The two readings are maxima observed during one timed run.
    paragraph = next(p for p in pages[2011].split("</p>") if "29 µs" in p)
    assert all(term in paragraph for term in ("cyclictest", "三小时", "最高延迟", "112 µs"))


def test_published_site_omits_stock_caveats():
    # Includes the industry archive and historic reports that are only in the snapshot.
    site = Path(__file__).resolve().parents[1] / "site"
    matches = {}
    for page in site.rglob("*.html"):
        match = STOCK_CAVEAT.search(page.read_text(encoding="utf-8"))
        if match:
            matches[str(page.relative_to(site))] = match.group()
    assert not matches, matches


def test_wait_queue_explanation_sets_state_before_rechecking_condition(article_source):
    page = render.render_mechanisms_html(Config(), "linux-execution-scheduling")
    path = page.split('<section id="path"', 1)[1].split("</section>", 1)[0]
    text = re.sub(r"<[^>]*>", "", path)
    assert "先登记等待者并设置等待状态，再检查完成条件" in text
    assert "条件仍未满足才调用调度器" in text
    assert "prepare_to_wait_event()" in text and "finish_wait()" in text
    assert "完成侧须先更新条件再唤醒" in text
    assert "include/linux/wait.h#L300-L329" not in path
    assert "include/linux/wait.h#L300-L329" in article_source('linux-execution-scheduling')


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
    assert '#paper-eurosys16-linux-scheduler' not in sources
    for context in ("EuroSys 2016", "CFS", "64 核", "NUMA", "Linux 3.17", "4.3", "EEVDF"):
        assert context in sources
    memory = render.render_mechanisms_html(Config(), "linux-memory")
    assert 'href="../conference-papers.html#paper-osdi16-ingens"' in memory
