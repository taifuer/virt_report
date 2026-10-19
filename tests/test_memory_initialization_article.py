"""Keep RAM initialization, address layout and the small mapping lab distinct."""
from pathlib import Path
import re

from virt_report.config import Config
from virt_report.render import render


def test_memory_article_follows_initialization_before_the_local_mapping_lab():
    page = render.render_mechanisms_html(Config(), "qemu-memory-region")
    assert "QEMU 内存初始化与地址空间管理" in page
    sections = re.findall(r'<section id="([^"]+)" class="mechanism-section">', page)
    assert sections == ["preface", "backing", "layout", "structures", "flatten", "mmio",
                        "topology", "lookup", "updates", "evolution", "sources"]
    for marker in ("MachineState.ram_size", "create_default_memdev()",
                   "memory-backend-ram", "machine_consume_memdev()",
                   "RAMBlock", "qemu_anon_ram_alloc()", "qemu_ram_mmap()",
                   "pc_q35_init()", "kvm_region_commit()"):
        assert marker in page
    assert page.index('id="flatten"') < page.index("64 KiB")
    assert "独立的 64 KiB 教学模型" in page
    assert "也与 x86 的 64 KiB 端口 I/O 空间无关" in page


def test_q35_example_distinguishes_capacity_backing_and_gpa():
    page = render.render_mechanisms_html(Config(), "qemu-memory-region")
    for marker in ("-machine pc-q35-10.2,accel=kvm", "-cpu host -m 8G",
                   "[0, 2 GiB)", "[2 GiB, 4 GiB)", "[4 GiB, 10 GiB)",
                   "[2 GiB, 8 GiB)", "B + 0x80001000",
                   "0x100001000 - 0x100000000 + 0x80000000",
                   "两个 RAM 别名不等于最终只有两个 FlatRange 或 KVM 内存槽",
                   "内存槽注册本身不会预建全部 EPT",
                   "物理页无需连续", "不会逐次调用 QEMU 的 memcpy",
                   "没有再申请一份 6 GiB 内存"):
        assert marker in page
    assert 'href="kvm-ept-fault.html"' in page


def test_new_diagrams_and_old_mapping_bookmarks_remain_available():
    page = render.render_mechanisms_html(Config(), "qemu-memory-region")
    for name in ("qemu-memory-initialization", "q35-memory-layout"):
        assert name in render.MECHANISM_DIAGRAM_NAMES
        assert f'data-diagram="{name}"' in page
        assert (render.ASSETS_DIR / f"{name}.svg").is_file()
        source = (Path(__file__).resolve().parents[1] / "virt_report/content/diagrams"
                  / f"{name}.mmd").read_text()
        assert "accTitle:" in source and "accDescr:" in source
    for anchor in ("preface", "topology", "lookup", "structures", "flatten", "mmio",
                   "updates", "updates-part-1", "updates-part-2", "updates-part-3",
                   "evolution", "sources", "memory-map-title"):
        assert f'id="{anchor}"' in page
    assert "data-memory-map-lab" in page


def test_startup_and_memory_overview_link_to_initialization_article():
    for slug in ("qemu-vm-startup", "memory-virtualization", "qemu-tcg"):
        page = render.render_mechanisms_html(Config(), slug)
        assert ('href="qemu-memory-region.html">QEMU 内存初始化与地址空间管理</a>'
                in page)
