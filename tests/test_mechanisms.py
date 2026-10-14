"""Offline editorial pages, source baselines and teaching-model regressions."""
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import pytest

from virt_report import mechanisms, server
from virt_report.config import Config, Render
from virt_report.render import render


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []
        self.scripts = []
        self.headings = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if tag == "a":
            self.links.append(attributes.get("href", ""))
        if tag == "script":
            self.scripts.append(attributes.get("src", ""))
        if tag == "h1":
            self.headings.append(tag)


def test_editorial_metadata_pins_each_repository_and_only_published_articles():
    articles = mechanisms.articles()
    assert len({article["slug"] for article in articles}) == len(articles)
    for article in articles:
        assert re.fullmatch(r"[a-z0-9-]+", article["slug"])
        assert mechanisms.get_topic(article["topic"]) is not None
        assert (render.TEMPLATES_DIR / article["template"]).is_file()
        assert article["baselines"]
        assert {item["project"] for item in article["baselines"]} <= {
            "QEMU", "Linux", "Libvirt", "SPDK",
        }
        for field in ("script", "stylesheet"):
            if article.get(field):
                assert article[field] in render.MECHANISM_ASSET_NAMES
        for baseline in article["baselines"]:
            assert re.fullmatch(r"[0-9a-f]{40}", baseline["commit"])
            assert baseline["url"].endswith(baseline["commit"])
    assert mechanisms.get_article("../base") is None
    assert mechanisms.get_topic("../base") is None


def test_editorial_topics_group_published_articles_and_omit_empty_sections(monkeypatch):
    metadata = {
        "topics": [
            {"slug": "migration", "title": "热迁移"},
            {"slug": "empty", "title": "尚无文章"},
        ],
        "articles": [
            {"slug": "first", "topic": "migration"},
            {"slug": "second", "topic": "migration"},
        ],
    }
    monkeypatch.setattr(mechanisms, "_content", lambda: metadata)
    mechanisms.topics.cache_clear()
    try:
        grouped = mechanisms.topics()
        assert len(grouped) == 1
        assert grouped[0]["slug"] == "migration"
        assert [article["slug"] for article in grouped[0]["articles"]] == ["first", "second"]
        assert mechanisms.get_topic("empty") is None
        assert "articles" not in metadata["topics"][0]
    finally:
        mechanisms.topics.cache_clear()


@pytest.mark.parametrize("slug", [article["slug"] for article in mechanisms.articles()])
def test_article_has_matching_toc_source_evidence_and_no_inline_scripts(slug):
    config = Config()
    article = mechanisms.get_article(slug)
    page = render.render_mechanisms_html(config, article["slug"])
    parsed = PageParser()
    parsed.feed(page)
    assert len(parsed.ids) == len(set(parsed.ids))
    assert len(parsed.headings) == 1
    assert f'{article["title"]} - virt-report' in page
    for section in article["sections"]:
        assert section["id"] in parsed.ids
        assert f'#{section["id"]}' in parsed.links
        assert f'<h2>{section["title"]}</h2>' in page
    assert all(src.startswith("../assets/") for src in parsed.scripts)
    for field in ("script", "stylesheet"):
        if article.get(field):
            assert f'../assets/{article[field]}?v=' in page
    assert "../assets/mechanisms.css?v=" in page
    assert "../assets/mechanism-viewer.css?v=" in page
    assert "../assets/mechanism-viewer.js?v=" in page
    if article.get("script"):
        assert page.index("assets/mechanism-viewer.js?v=") < page.index(
            f'assets/{article["script"]}?v=')
    assert f'../mechanisms.html#{article["topic"]}' in parsed.links
    topic = mechanisms.get_topic(article["topic"])
    assert f'{topic["title"]}原理</a>' in page
    for link in topic.get("related", []):
        assert f'../{link["path"]}' in parsed.links
        assert f'{link["label"]}</a>' in page
    assert "热迁移专题</a>" not in page
    assert page.index('id="preface"') < page.index('class="mechanism-baselines"')
    assert "该限定用于读清一条完整路径" not in page
    assert "热迁移转移的是一台正在运行的虚拟机" not in page
    assert "在线文档随开发版本更新" not in page
    assert "在线文档持续更新，正文实现以固定版本为准" not in page
    assert "本文细节以固定源码为准" not in page
    assert "#L" in page
    assert any(f'/blob/{baseline["commit"]}/' in page for baseline in article["baselines"])
    assert "/root/codes/" not in page
    assert all(link[1:] in parsed.ids for link in parsed.links if link.startswith("#"))


def test_articles_cover_distinct_mechanisms_and_link_to_each_other():
    memory = render.render_mechanisms_html(Config(), "qemu-memory-migration")
    stream = render.render_mechanisms_html(Config(), "qemu-migration-stream")
    for page, target in ((memory, "qemu-migration-stream.html"),
                         (stream, "qemu-memory-migration.html")):
        parsed = PageParser()
        parsed.feed(page)
        assert any(link.split("/")[-1] == target for link in parsed.links)
    assert 'data-migration-interactive hidden' in memory
    assert 'class="mechanism-diagram"' in memory
    assert "Mermaid 源码" in memory
    assert "/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/" in memory
    for marker in ("downtime-limit", "expected-downtime", "auto-converge", "dirty-limit", "MultiFD"):
        assert marker in memory
    for marker in ("QEMU_VM_FILE_MAGIC", "QEMU_VM_SECTION_", "SECTION_START", "SECTION_END",
                   "QEMU_VM_SECTION_FOOTER", "RAM_SAVE_FLAG_EOS", "VMDESCRIPTION",
                   "SWITCHOVER_START"):
        assert marker in stream
    assert "../assets/migration-wire-layout.svg?v=" in stream
    assert "运行中每个传输步写入的页面数" not in memory


@pytest.mark.parametrize("slug", [article["slug"] for article in mechanisms.articles()
                                 if article["topic"] == "device-virtualization"])
def test_device_article_related_navigation_does_not_reuse_migration_links(slug):
    page = render.render_mechanisms_html(Config(), slug)
    navigation = re.search(
        r'<nav\b[^>]*\bclass="mechanism-related"[^>]*>(.*?)</nav>', page, re.DOTALL,
    ).group(0)
    assert "设备与 I/O 虚拟化原理</a>" in navigation
    assert "../mechanisms.html#device-virtualization" in navigation
    assert "热迁移动态" not in navigation
    assert "topics/migration" not in navigation


def test_multifd_article_connects_to_existing_migration_articles():
    memory = render.render_mechanisms_html(Config(), "qemu-memory-migration")
    multifd = render.render_mechanisms_html(Config(), "qemu-multifd")
    assert 'href="qemu-multifd.html"' in memory
    assert 'href="qemu-memory-migration.html"' in multifd
    assert 'href="qemu-migration-stream.html"' in multifd


def test_existing_articles_link_to_backend_and_dirty_log_explanations():
    memory = render.render_mechanisms_html(Config(), "qemu-memory-migration")
    network = render.render_mechanisms_html(Config(), "qemu-network-tx")
    assert 'href="kvm-dirty-ring.html"' in memory
    assert 'href="qemu-vhost-net.html"' in network
    assert 'href="qemu-postcopy.html"' in memory
    vhost = render.render_mechanisms_html(Config(), "qemu-vhost-net")
    assert 'href="qemu-network-tx.html#virtqueue"' in vhost
    assert 'href="qemu-vfio-dma.html"' in vhost


def test_memory_and_interrupt_articles_connect_to_existing_paths():
    for source, target in (
        ("kvm-dirty-ring", "kvm-ept-fault.html#structures"),
        ("qemu-vfio-dma", "kvm-ept-fault.html"),
        ("qemu-network-tx", "kvm-virtio-interrupt.html"),
        ("qemu-vhost-net", "kvm-virtio-interrupt.html#irqfd"),
        ("kvm-virtio-interrupt", "qemu-network-tx.html#virtqueue"),
        ("kvm-virtio-interrupt", "qemu-vhost-net.html#notification"),
    ):
        assert f'href="{target}"' in render.render_mechanisms_html(Config(), source)


@pytest.mark.parametrize("slug,markers", [
    ("qemu-memory-migration", ["RAMBlock.clear_bmap", "PageSearchStatus.block", "当前 bmap"]),
    ("qemu-migration-stream", ["SaveStateEntry", "VMStateField", "load_section_id"]),
    ("qemu-multifd", ["MultiFDSendData", "pending_job", "offset = [0x2000, 0x7000]"]),
    ("qemu-network-tx", ["data-tx-split-layout", "data-tx-queue-start", "65535",
                         "data-tx-desc-field=\"next\"", "last_avail_idx", "last_used_idx"]),
    ("qemu-vhost-net", ["desc_user_addr", "mmap_offset", "SET_VRING_BASE"]),
    ("kvm-dirty-ring", ["kvm_dirty_gfn", "offset: 3", "data-dirty-ring-pending"]),
    ("qemu-vfio-dma", ["IOMMU_IOAS_MAP", "HWPT", "IOVA", "data-vfio-lab"]),
    ("qemu-postcopy", ["UFFDIO_COPY", "receivedmap", "data-postcopy-lab"]),
    ("kvm-ept-fault", ["kvm_userspace_memory_region2", "kvm_memory_slot", "0x00403080",
                       "0x21000080", "mmu_invalidate_seq", "is_page_fault_stale"]),
    ("kvm-virtio-interrupt", ["used_event", "0xfee01000", "KVM_SIGNAL_MSI", "KVM_IRQFD",
                              "IRR[0x51]", "ISR[0x51]", "apic_ack_edge"]),
    ("qemu-qom-device", ["TypeInfo", "TypeImpl", "VirtIONetPCI", "Object.class",
                         "instance_finalize", "realized_set_allowed"]),
    ("qemu-vm-startup", ["KVM_CREATE_VCPU", "0xfffffff0", "KVM_MP_STATE_UNINITIALIZED",
                         "cpu_can_run", "prelaunch", "INIT / SIPI"]),
    ("libvirt-rpc", ["remote_nonnull_domain", "virDomainObj", "0x20008086",
                     "length=56", "length=60", "serial=42", "REPLY / ERROR"]),
    ("qemu-pcie-hotplug", ["pending_deleted_expires_ms", "DEVICE_DELETED", "PDS", "PDC",
                           "unplug_request", "Slot Control", "netdev_del"]),
    ("libvirt-domain-start", ["VIR_ASYNC_JOB_START", "virDomainObj", "virCommand",
                              "virCommandHandshakeNotify", "qemuProcessHandleResume",
                              "PAUSED / STARTING_UP", "SHUTOFF"]),
    ("qemu-cpr-transfer", ["CprFd", "SCM_RIGHTS", "0x51435052", "cpr_state_load",
                           "migration_stop_vm", "postmigrate", "aux-ram-share=on"]),
    ("qemu-memory-region", ["MemoryRegionSection", "offset_in_region", "alias_offset",
                            "data-memory-map-lab", "0x3004", "kvm_region_commit"]),
    ("qemu-virtio-blk", ["virtio_blk_outhdr", "sector = 2048", "mr_next",
                         "len: 1", "BDRV_REQ_FUA", "cache.no-flush"]),
    ("qemu-aio-iothread", ["ThreadPoolAio", "THREAD_DONE", "completion_bh",
                           "has_returned", "vq_aio_context", "drain"]),
    ("qemu-virtio-mem", ["virtio_mem_req", "usable_region_size", "sb_states",
                         "alloc_contig_range", "data-virtio-mem-lab", "RamDiscardManager"]),
    ("kvm-vcpu-halt", ["kvm_vcpu_halt", "rcuwait", "TASK_INTERRUPTIBLE",
                       "KVM_CAP_HALT_POLL", "data-halt-poll-lab"]),
    ("kvm-hugepages", ["goal_level", "make_small_spte", "0x80003000",
                       "recover_huge_pages_range", "data-hugepage-lab"]),
    ("qemu-qcow2", ["QCOW_OFLAG_COPIED", "refcount", "0x20031000",
                    "QCowL2Meta", "data-qcow2-map-lab"]),
    ("spdk-vhost-user-blk", ["spdk_vhost_user_blk_task", "spdk_bdev_io_wait_entry",
                            "spdk_nvme_ns_cmd_write_with_md", "last_avail_idx",
                            "VIRTIO_RING_F_EVENT_IDX", "NLB = 0"]),
    ("qemu-tcg", ["TranslationBlock", "TCGContext", "CPUArchState", "goto_tb",
                   "lookup_and_goto_ptr", "data-tcg-tb-lab"]),
])
def test_articles_expose_key_structures_and_operations(slug, markers):
    page = render.render_mechanisms_html(Config(), slug)
    for marker in markers:
        assert marker in page


@pytest.mark.parametrize("slug", [article["slug"] for article in mechanisms.articles()
                                 if article["topic"] == "migration"])
def test_article_related_content_keeps_only_two_migration_links(slug):
    page = render.render_mechanisms_html(Config(), slug)
    matched = re.search(
        r'<nav\b[^>]*\bclass="mechanism-related"[^>]*>(.*?)</nav>',
        page, re.DOTALL,
    )
    assert matched is not None
    navigation = matched.group(0)
    content = matched.group(1)
    assert 'aria-label="相关内容"' in navigation
    assert re.match(r'\s*<span\b[^>]*>相关内容</span>\s*<a\b', content)
    parsed = PageParser()
    parsed.feed(navigation)
    assert parsed.links == [
        "../mechanisms.html#migration",
        "../topics/migration/index.html",
    ]
    assert re.findall(r'<a\b[^>]*>([^<]+)</a>', navigation) == ["热迁移原理", "热迁移动态"]
    for removed in ("相关版本", "会议研究", "全部原理文章"):
        assert removed not in navigation


def test_catalogue_has_one_title_link_per_article_without_redundant_cta():
    page = render.render_mechanisms_html(Config())
    assert "阅读正文与图解" not in page
    assert "源码基线" not in page
    assert "QEMU 10.2.0" not in page
    for article in mechanisms.articles():
        assert page.count(f'<h4><a href="mechanisms/{article["slug"]}.html">') == 1
    assert 'id="migration" aria-labelledby="mechanism-topic-migration"' in page
    assert '<h3 id="mechanism-topic-migration">热迁移</h3>' in page
    assert '<h3 id="mechanism-topic-device-virtualization">设备与 I/O 虚拟化</h3>' in page
    assert page.count('class="mechanism-topic"') == len(mechanisms.topics())


def test_catalogue_about_and_export_work_without_database_and_preserve_reports(tmp_path):
    config = Config(render=Render(output_dir=tmp_path))
    historical = tmp_path / "daily" / "2026-10-12.html"
    historical.parent.mkdir()
    historical.write_text("historical report", encoding="utf-8")
    exported = render.render_mechanisms(config)
    assert {path.relative_to(tmp_path).as_posix() for path in exported} == {
        "mechanisms.html", *(f'mechanisms/{article["slug"]}.html'
                             for article in mechanisms.articles()),
    }
    assert historical.read_text() == "historical report"
    for name in render.MECHANISM_ASSET_NAMES:
        assert (tmp_path / "assets" / name).read_bytes() == (
            render.ASSETS_DIR / name).read_bytes()
    assert 'href="mechanisms/qemu-memory-migration.html"' in (
        tmp_path / "mechanisms.html").read_text()
    assert 'href="mechanisms.html">查看原理' in render.render_about_html(config)


@pytest.mark.parametrize("path,status,marker", [
    ("/mechanisms.html", 200, "Virtualization Internals"),
    ("/mechanisms/qemu-memory-migration.html", 200, "QEMU 预拷贝迁移的内存迭代与停机控制"),
    ("/mechanisms/qemu-migration-stream.html", 200, "QEMU 迁移流格式与状态恢复"),
    ("/mechanisms/qemu-multifd.html", 200, "QEMU MultiFD 的任务分发与跨通道同步"),
    ("/mechanisms/qemu-network-tx.html", 200, "QEMU 网卡模拟与 virtio-net 的发包路径"),
    ("/mechanisms/qemu-vhost-net.html", 200, "virtio-net 的后端处理：vhost-net 与 vhost-user"),
    ("/mechanisms/kvm-dirty-ring.html", 200, "KVM 脏页日志：位图与 Dirty Ring"),
    ("/mechanisms/qemu-postcopy.html", 200, "QEMU 后拷贝迁移的缺页处理与页面传输"),
    ("/mechanisms/qemu-vfio-dma.html", 200, "VFIO 设备直通的 DMA 地址转换与隔离"),
    ("/mechanisms/kvm-ept-fault.html", 200, "KVM 内存映射与 EPT 缺页处理"),
    ("/mechanisms/kvm-virtio-interrupt.html", 200, "virtio 完成通知与 KVM 中断注入"),
    ("/mechanisms/qemu-qom-device.html", 200, "QEMU 对象模型与设备初始化"),
    ("/mechanisms/qemu-vm-startup.html", 200, "QEMU 虚拟机启动与 vCPU 初始化"),
    ("/mechanisms/libvirt-rpc.html", 200, "Libvirt RPC 的编码与调用分发"),
    ("/mechanisms/libvirt-domain-start.html", 200, "Libvirt 虚拟机启动的进程与状态管理"),
    ("/mechanisms/qemu-pcie-hotplug.html", 200, "QEMU PCIe 设备热插拔与异步移除"),
    ("/mechanisms/qemu-cpr-transfer.html", 200, "QEMU 同机更新的内存与文件描述符交接"),
    ("/mechanisms/qemu-memory-region.html", 200, "QEMU MemoryRegion 的地址映射与访问分发"),
    ("/mechanisms/qemu-virtio-blk.html", 200, "virtio-blk 的请求处理与写入完成"),
    ("/mechanisms/qemu-aio-iothread.html", 200, "QEMU AioContext、协程与 IOThread"),
    ("/mechanisms/qemu-virtio-mem.html", 200, "virtio-mem 的内存添加与回收"),
    ("/mechanisms/kvm-vcpu-halt.html", 200, "KVM vCPU 的阻塞、轮询与唤醒"),
    ("/mechanisms/kvm-hugepages.html", 200, "KVM 大页映射的建立、拆分与恢复"),
    ("/mechanisms/qemu-qcow2.html", 200, "QEMU qcow2 的地址映射与写时复制"),
    ("/mechanisms/spdk-vhost-user-blk.html", 200, "SPDK vhost-user-blk 的请求处理路径"),
    ("/mechanisms/qemu-tcg.html", 200, "QEMU TCG 的翻译块与执行循环"),
    ("/mechanisms/unknown.html", 404, "Not found"),
    ("/mechanisms/../../base.html", 404, "404"),
    ("/assets/mechanisms.js", 200, "createState"),
    ("/assets/mechanisms.css", 200, "prefers-reduced-motion"),
    ("/assets/precopy-lab.css", 200, "precopy-"),
    ("/assets/migration-stream.css", 200, "stream-"),
    ("/assets/migration-stream.js", 200, "function"),
    ("/assets/multifd.css", 200, "multifd-"),
    ("/assets/multifd.js", 200, "function"),
    ("/assets/device-tx.css", 200, "device-tx"),
    ("/assets/device-tx.js", 200, "function"),
    ("/assets/vhost-path.css", 200, "vhost-"),
    ("/assets/vhost-path.js", 200, "function"),
    ("/assets/dirty-ring.css", 200, "dirty-"),
    ("/assets/dirty-ring.js", 200, "function"),
    ("/assets/postcopy.css", 200, "postcopy-"),
    ("/assets/postcopy.js", 200, "function"),
    ("/assets/vfio-dma.css", 200, "vfio-"),
    ("/assets/vfio-dma.js", 200, "function"),
    ("/assets/halt-poll.js", 200, "function"),
    ("/assets/halt-poll.css", 200, "halt-poll"),
    ("/assets/hugepage-map.js", 200, "function"),
    ("/assets/hugepage-map.css", 200, "hugepage"),
    ("/assets/qcow2-map.js", 200, "function"),
    ("/assets/qcow2-map.css", 200, "qcow2-map"),
    ("/assets/tcg-tb.js", 200, "function"),
    ("/assets/tcg-tb.css", 200, "tcg-tb"),
    ("/assets/mechanism-viewer.css", 200, "mechanism-"),
    ("/assets/mechanism-viewer.js", 200, "function"),
    ("/assets/migration-architecture.svg", 200, "<svg"),
    ("/assets/migration-precopy-sequence.svg", 200, "<svg"),
    ("/assets/migration-wire-layout.svg", 200, "<svg"),
])
def test_editorial_routes_do_not_open_database(monkeypatch, path, status, marker):
    def forbidden(*args, **kwargs):
        pytest.fail("Editorial pages must not open the database")

    monkeypatch.setattr(server.db, "connect", forbidden)
    handler_type = server.make_handler(Config())
    handler = object.__new__(handler_type)
    handler.path = path
    responses = []
    handler._send = lambda code, body, *_args, **_kwargs: responses.append((code, body))
    handler._dispatch(head_only=False)
    code, body = responses[0]
    assert code == status
    assert marker in (body.decode() if isinstance(body, bytes) else body)


def test_editorial_render_is_cached_and_rejects_unregistered_templates():
    render._render_mechanism_page.cache_clear()
    first = render.render_mechanisms_html(Config(), "qemu-memory-migration")
    second = render.render_mechanisms_html(Config(), "qemu-memory-migration")
    assert first == second
    assert render._render_mechanism_page.cache_info().hits == 1
    assert render.render_mechanisms_html(Config(), "base.html") is None


def test_diagrams_are_self_contained_svg_with_accessible_descriptions():
    for name in (*render.MECHANISM_DIAGRAM_NAMES, *render.MECHANISM_LAYOUT_NAMES):
        svg = (render.ASSETS_DIR / f"{name}.svg").read_text(encoding="utf-8")
        root = ElementTree.fromstring(svg)
        assert root.tag == "{http://www.w3.org/2000/svg}svg"
        assert root.get("viewBox")
        assert list(root.iter("{http://www.w3.org/2000/svg}title"))
        assert list(root.iter("{http://www.w3.org/2000/svg}desc"))
        for node in root.iter():
            assert not node.tag.endswith(("script", "foreignObject"))
            assert not any(key.lower().startswith("on") for key in node.attrib)
            assert not any(value.startswith(("http:", "https:", "//"))
                           for key, value in node.attrib.items() if key.endswith("href"))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for browser-model tests")
@pytest.mark.parametrize("filename", ["test_migration_model.cjs", "test_migration_stream.cjs",
                                     "test_multifd.cjs", "test_device_tx.cjs",
                                     "test_vhost_path.cjs", "test_dirty_ring.cjs",
                                     "test_postcopy.cjs", "test_vfio_dma.cjs",
                                     "test_memory_map.cjs", "test_virtio_mem.cjs",
                                     "test_halt_poll.cjs", "test_hugepage_map.cjs",
                                     "test_qcow2_map.cjs", "test_tcg_tb.cjs"])
def test_migration_model_state_invariants(filename):
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name(filename))],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
