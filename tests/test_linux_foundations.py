"""Linux prerequisites reuse the article shell and keep completion boundaries."""
import re

import pytest

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render


SLUGS = [
    "linux-kernel-evolution", "linux-boot", "linux-execution-scheduling", "linux-memory", "linux-interrupts",
    "linux-block-io", "linux-network-io",
]
COMMIT = "7d0a66e4bb9081d75c82ec4957c50034cb0ea449"


@pytest.mark.parametrize("slug", SLUGS)
def test_linux_prerequisites_share_navigation_and_pinned_source_baseline(slug):
    article = mechanisms.get_article(slug)
    assert article["topic"] == "linux-foundations"
    assert article["baselines"][0]["commit"] == COMMIT
    assert not article.get("execution_demo")
    page = render.render_mechanisms_html(Config(), slug)
    assert "mechanism-article" in page
    assert "<style" not in page
    assert f"github.com/torvalds/linux/blob/{COMMIT}/" in page
    for section in article["sections"]:
        assert f'id="{section["id"]}" class="mechanism-section"' in page
        assert section["title"] in page
    rendered_sections = re.findall(r'<section id="([^"]+)" class="mechanism-section">', page)
    assert rendered_sections == [section["id"] for section in article["sections"]]
    assert len(rendered_sections) == (9 if slug == 'linux-boot' else 10 if slug in {
        "linux-execution-scheduling", "linux-memory", "linux-kernel-evolution",
    } else 8)
    assert rendered_sections[1] == {'linux-kernel-evolution': 'privilege',
                                    'linux-boot': 'firmware'}.get(slug, 'history')
    figures = re.findall(r'data-diagram="(linux-[^"]+)"', page)
    assert len(figures) == {'linux-kernel-evolution': 4, 'linux-boot': 1}.get(slug, 3)
    assert len(figures) == len(set(figures))
    assert "只读观察" in page


def test_linux_prerequisites_are_accessible_from_directory_without_a_reading_list():
    page = render.render_mechanisms_html(Config(), "virtualization-basics")
    directory = render.render_mechanisms_html(Config())
    for slug in SLUGS:
        assert f'href="mechanisms/{slug}.html"' in directory
    assert '<section id="reading"' not in page


def test_linux_overview_covers_history_boundaries_and_subsystem_cooperation():
    page = render.render_mechanisms_html(Config(), "linux-kernel-evolution")
    for phrase in ("1991", "1994", "1996", "2003", "SMP", "单体内核", "发行版",
                   "namespace", "cgroup", "2.6.20", "2.6.24", "回移", "合并窗口",
                   "同一线程", "唤醒不等于已经返回", "RELNOTES-0.01"):
        assert phrase in page
    for phrase in ('用户空间', '内核空间', 'CPL 3', 'CPL 0', 'mm_struct', '系统调用'):
        assert phrase in page
    assert re.search(r'"overview":\s*"linux-kernel-evolution"',
                     (render.ASSETS_DIR.parents[1] / "content/mechanisms.json").read_text())


@pytest.mark.parametrize("slug,phrases", [
    ("linux-execution-scheduling", ["TASK_RUNNING", "EEVDF", "VM-exit", "不一定更换任务"]),
    ("linux-memory", ["TLB", "VMA", "COW", "零页", "Guest"]),
    ("linux-interrupts", ["不是 Linux IRQ", "ksoftirqd", "不是远端收到确认", "同一完成事件"]),
    ("linux-block-io", ["FLUSH/FUA", "O_DIRECT", "不自动等于持久化", "块设备写请求成功完成"]),
    ("linux-network-io", ["外部可随时发来", "virtio-net TX Used", "远端应用已读", "不同环节"]),
])
def test_linux_prerequisites_preserve_critical_distinctions(slug, phrases):
    page = render.render_mechanisms_html(Config(), slug)
    for phrase in phrases:
        assert phrase in page


@pytest.mark.parametrize("slug,phrases,evidence", [
    ("linux-execution-scheduling", ["O(1)", "2.6.23", "vruntime", "lag", "cpu.max"], ["sched-design.txt"]),
    ("linux-memory", ["Maple Tree", "MGLRU", "256", "vmalloc()"], ["multigen_lru.rst", "v2.6.12"]),
    ("linux-interrupts", ["共享 INTx", "PREEMPT_RT", "EOI", "竞态"], ["dma-api-howto.rst"]),
    ("linux-block-io", ["机械盘", "blk_mq_hw_ctx", "tag", "io_uring", "100 μs", "背压"], []),
    ("linux-network-io", ["2002", "RSS", "RFS", "GRO", "UMEM", "zero-copy"], ["NAPI_HOWTO"]),
])
def test_linux_articles_cover_evolution_and_engineering_tradeoffs(slug, phrases, evidence, article_source):
    page = render.render_mechanisms_html(Config(), slug)
    history = page.split('<section id="history"', 1)[1].split("</section>", 1)[0]
    assert "<table>" in history
    assert '<a href="https://' not in history
    assert '<a href="https://' in page.split('id="sources"', 1)[1]
    for phrase in phrases:
        assert phrase in page
    for reference in evidence:
        assert reference in article_source(slug)


@pytest.mark.parametrize("slug,phrases", [
    ("linux-execution-scheduling", ["context_switch()", "lagᵢ", "vA = 0，vB = 1", "update_deadline()"]),
    ("linux-memory", ["0x400123", "0x12a123", "父进程页表", "子进程页表", "VMA 本身不允许写"]),
    ("linux-interrupts", ["IRQ 137", "0x51", "IRQ_WAKE_THREAD", "IRQ 计数增加 1"]),
    ("linux-block-io", ["pread(fd, buf, 4096, 4096)", "起点为 800", "REQ_PREFLUSH", "REQ_FUA"]),
    ("linux-network-io", ["sk_data_ready", "ndo_start_xmit()", "NETDEV_TX_BUSY", "剩余 36 个"]),
])
def test_linux_articles_keep_worked_examples_and_ownership_boundaries(slug, phrases):
    page = render.render_mechanisms_html(Config(), slug)
    for phrase in phrases:
        assert phrase in page


def test_process_title_keeps_existing_permalink_and_directory_consistency():
    slug = "linux-execution-scheduling"
    article = mechanisms.get_article(slug)
    assert article["title"] == "Linux 进程：执行与调度"
    for page in (render.render_mechanisms_html(Config(), slug),
                 render.render_mechanisms_html(Config())):
        assert article["title"] in page
        assert "Linux 执行与调度：线程、阻塞和 CPU 时间" not in page
    assert f'href="mechanisms/{slug}.html"' in render.render_mechanisms_html(Config())


@pytest.mark.parametrize("slug,phrases,evidence", [
    ("linux-execution-scheduling", [
        "CLONE_VM", "CLONE_FILES", "CLONE_THREAD", "files_struct", "mm_struct",
        "gettid()", "FD_CLOEXEC", "de_thread()", "waitpid()", "僵尸", "孤儿",
        "FUTEX_WAIT", "EAGAIN", "200000 100000", "schedstat", "throttled_usec",
    ], []),
    ("linux-memory", [
        "malloc()", "MAP_PRIVATE", "MAP_SHARED", "mprotect()", "munmap()",
        "TLB shootdown", "SIGSEGV", "SIGBUS", "PSS", "swap entry", "order 9",
        "memory.high", "memory.max", "memory.swap.max", "memory.pressure",
    ], []),
    ("linux-interrupts", [
        "spin_lock_irqsave()", "struct completion", "wait_for_completion()",
        "等待超时", "迟到的完成回调",
    ], ["Documentation/scheduler/completion.rst"]),
    ("linux-block-io", ["pread(2)", "共享偏移", "字节数可以少于", "用户页", "缓冲区有效"], []),
    ("linux-network-io", ["EPOLLET", "EAGAIN", "空报文", "接收窗口"], ["epoll.7.html"]),
])
def test_linux_articles_explain_lifecycle_and_resource_limits(slug, phrases, evidence, article_source):
    page = render.render_mechanisms_html(Config(), slug)
    for phrase in phrases:
        assert phrase in page
    for reference in evidence:
        assert reference in article_source(slug)


def test_general_kernel_research_has_sources_and_historical_scope():
    overview = render.render_mechanisms_html(Config(), "linux-kernel-evolution")
    assert "An Analysis of Linux Scalability to Many Cores" in overview
    assert "usenix.org/conference/osdi10/analysis-linux-scalability-many-cores" in overview
    assert "48 核" in overview and "2.6.35-rc5" in overview
    memory = render.render_mechanisms_html(Config(), "linux-memory")
    assert 'href="https://pdos.csail.mit.edu/papers/radixvm:eurosys13.pdf"' in memory
    assert "EuroSys 2013" in memory and "研究内核" in memory
