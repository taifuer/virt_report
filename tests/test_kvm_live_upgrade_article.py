"""Orthus mechanism, publication evidence and cross-article boundaries."""
import re

from virt_report import mechanisms
from virt_report.config import Config, Render, Storage
from virt_report.render import render

ORTHUS_DOI = "https://doi.org/10.1145/3297858.3304034"


def _section(page: str, section: str) -> str:
    return re.search(
        rf'<section\b[^>]*\bid="{section}"[^>]*>(.*?)</section>', page, re.S,
    ).group(1)


def test_upgrade_category_lists_qemu_and_kvm_without_using_cpr_as_an_overview():
    topic = mechanisms.get_topic("live-upgrade")
    assert topic["overview"] == "live-upgrade-overview"
    assert topic["overview_article"]["slug"] == "live-upgrade-overview"
    assert [article["slug"] for article in topic["detail_articles"]] == [
        "qemu-cpr-transfer", "kvm-live-upgrade",
    ]
    # Keep the old article address working without using it as the category entry.
    assert mechanisms.get_redirect("live-upgrade")["target"] == "qemu-cpr-transfer"
    directory = render.render_mechanisms_html(Config())
    category = _section(directory, "live-upgrade")
    heading = re.search(r"<h3[^>]*>(.*?)</h3>", category, re.S).group(1)
    assert 'href="mechanisms/live-upgrade-overview.html"' in heading
    assert "热升级" in heading
    assert "KVM 热补丁与宿主内核交接" not in category
    for title, slug in (
        ("QEMU 热升级：CPR 的资源交接与状态恢复", "qemu-cpr-transfer"),
        ("KVM 热升级：Orthus 与 Dual KVM", "kvm-live-upgrade"),
    ):
        assert f'href="mechanisms/{slug}.html"' in category
        assert title in category


def test_orthus_uses_publication_evidence_and_retains_cpr_cross_links():
    article = mechanisms.get_article("kvm-live-upgrade")
    assert article["title"] == "KVM 热升级：Orthus 与 Dual KVM"
    assert not article["baselines"]
    assert article["publications"][0]["url"] == ORTHUS_DOI
    assert "script" not in article and "execution_demo" not in article
    page = render.render_mechanisms_html(Config(), "qemu-cpr-transfer")
    assert 'href="kvm-live-upgrade.html"' in page
    assert 'href="kvm-live-upgrade.html#device-handover"' in page
    assert "#cpr-luo" not in page


def test_orthus_follows_the_problem_design_example_results_reading_order():
    article = mechanisms.get_article("kvm-live-upgrade")
    page = render.render_mechanisms_html(Config(), article["slug"])
    sections = [section["id"] for section in article["sections"]]
    assert sections == ["preface", "alternatives", "dual-kvm", "grafting",
                        "device-handover", "downtime", "evolution", "hyperfresh",
                        "compatibility", "sources"]
    assert re.findall(r'<section id="([^"]+)" class="mechanism-section">', page) == sections
    preface = _section(page, "preface")
    assert "运行数据库服务" in preface and "维护窗口" in preface
    alternatives = _section(page, "alternatives")
    for phrase in ("停机更新", "跨主机热迁移", "只替换 QEMU", "1 GiB/s",
                   "约 8 秒", "不能直接算成停机时间", "三个难点"):
        assert phrase in alternatives
    assert "不属于 Orthus 论文当年的实验对照" in alternatives
    assert "8 GiB" in _section(page, "grafting")
    assert "报文 P" in _section(page, "device-handover")
    assert "本文的 8 GiB 示例" in _section(page, "downtime")
    headings = re.findall(r"<h[23][^>]*>(.*?)</h[23]>", page, re.S)
    assert not any("？" in heading or "?" in heading for heading in headings)


def test_orthus_explains_coexistence_grafting_and_device_handover():
    page = render.render_mechanisms_html(Config(), "kvm-live-upgrade")
    preface = _section(page, "preface")
    assert "论文依据：" in preface and "Orthus · ASPLOS 2019" in preface
    assert ORTHUS_DOI in _section(page, 'sources')
    assert "源码版本：" not in page
    assert "4 vCPU、8 GiB" in preface
    assert "不代表论文的测试配置" in preface
    dual = _section(page, "dual-kvm")
    for phrase in ("kvm.ko", "/dev/kvm0", "/dev/kvm1", "全局变量按实例隔离",
                   "最后一台", "不在本次替换范围内"):
        assert phrase in dual
    grafting = _section(page, "grafting")
    for phrase in ("savevm_state", "loadvm_state", "MAP_KVM_RESERVED", "内核扩展",
                   "不能再直接恢复旧 vCPU", "fork()", "execv()"):
        assert phrase in grafting
    device = _section(page, "device-handover")
    for phrase in ("VFIO connector", "IOMMU", "DMA", "遗漏中断", "补充注入虚拟 IRQ"):
        assert phrase in device
    assert "eventfd 提供事件通知，不保存报文载荷" in device
    assert "原有 IOMMU 映射" in device
    for diagram in ("orthus-dual-kvm", "orthus-vm-grafting", "orthus-device-handover"):
        assert f"../assets/{diagram}.svg" in page
    for obsolete in ("caretaker", "cpr-luo", "kernel-handoff", "livepatch"):
        assert f'<section id="{obsolete}"' not in page


def test_orthus_limits_metrics_and_compares_a_directly_related_followup():
    page = render.render_mechanisms_html(Config(), "kvm-live-upgrade")
    downtime = _section(page, "downtime")
    for phrase in ("16 vCPU、32 GB", "29–37 ms", "100 次", "800 次", "GPU", "27.3 s",
                   "268 ms", "12 ms", "4.5 万台 VM", "45 分钟", "分三天灰度推进"):
        assert phrase in downtime
    assert "保存状态到恢复执行" in _section(page, "grafting")
    assert "两次记录的规模与运维条件不同" in downtime
    assert "不能直接用日历时间相除得到算法加速比" in downtime
    assert "两行的负载和采样范围分别列出" in downtime
    evolution = _section(page, "evolution")  # Preserve the old subsection link.
    for phrase in ("KVM Forum 2020", "2022", "pi_desc", "PIR", "Interrupt Remapping Table",
                   "VT-d Posted Interrupt", "eventfd", "共享内存并发", "11–38 ms"):
        assert phrase in evolution
    assert "gitlab.com/qemu-project/kvm-forum/-/raw/main/_attachments/2020/" in _section(page, 'sources')
    compatibility = _section(page, "compatibility")
    assert "薄 <code>kvm.ko</code>" in compatibility
    assert "不兼容的恢复失败" in compatibility
    assert "状态兼容" in compatibility
    assert "10.1145/1122445.1122456" not in page  # Placeholder in the author draft.
    assert "https://dl.acm.org/doi/pdf/10.1145/3297858.3304034" in page


def test_upgrade_followups_distinguish_kvm_replacement_from_retained_layers():
    page = render.render_mechanisms_html(Config(), "kvm-live-upgrade")
    evolution = _section(page, "evolution")
    for phrase in ("DPDK", "SPDK", "共享文件描述符", "配置在暂停前预保存",
                   "后端崩溃", "内存地址", "补充 IRQ", "QEMU-only", "保留旧 KVM"):
        assert phrase in evolution
    assert "kvm-forum-2022-Deep-Optimization-of-VMM-Live-Upgrade.pdf" in _section(page, 'sources')
    assert "../assets/kvm-upgrade-vhost-handover.svg" in evolution
    hyperfresh = _section(page, "hyperfresh")
    for phrase in ("L0 hyperplexor", "L1", "L2", "APSys 2017", "VEE 2019",
                   "同一工作的演进", "原 Guest 内存页", "约 100 ms", "约 10 ms",
                   "L0 的更新不在此次接管范围内", "不能直接套到普通单层 KVM"):
        assert phrase in hyperfresh
    assert "../assets/hyperfresh-handoff.svg" in hyperfresh
    assert 'href="kvm-nested-vmx.html"' in hyperfresh
    for doi in ("10.1145/3124680.3124734", "10.1145/3313808.3313821"):
        assert f'href="https://doi.org/{doi}"' in _section(page, 'sources')
    compatibility = _section(page, "compatibility")
    for phrase in ("Orthus / Dual KVM", "字节跳动 VMM Live Upgrade", "HyperFresh",
                   "保留 L0", "vCPU 只在一端运行"):
        assert phrase in compatibility


def test_upgrade_article_keeps_the_agreed_scope_and_plain_headings():
    page = render.render_mechanisms_html(Config(), "kvm-live-upgrade")
    body = "".join(_section(page, section["id"])
                   for section in mechanisms.get_article("kvm-live-upgrade")["sections"])
    for excluded in ("kexec", "Hy-FiX", "Fast Restart", "KHO", "LUO", "Caretaker"):
        assert excluded.lower() not in body.lower()
    assert not re.search(r"不是[^。！？]*而是", re.sub(r"<[^>]+>", "", body))
    assert "宿主基础内核或底层管理层保持运行" in _section(page, "preface")
    sources = _section(page, "sources")
    for link in ("bagdi17hyperfresh.pdf", "doddamani19fast.pdf",
                 "../conference-papers.html?q=HyperFresh#paper-vee19-live-replacement"):
        assert link in sources


def test_orthus_exports_without_runtime_data(tmp_path):
    config = Config(storage=Storage(db_path=tmp_path / "no-database/report.db"),
                    render=Render(output_dir=tmp_path / "site"))
    render.render_mechanisms(config)
    page = (config.output_dir / "mechanisms/kvm-live-upgrade.html").read_text()
    assert 'href="qemu-cpr-transfer.html"' in page
    assert 'href="#device-handover"' in page
    assert ORTHUS_DOI in page
    assert not config.db_path.parent.exists()
