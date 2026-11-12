"""Creation, upgrade and kernel basics stay complete without link-list coupling."""
import re
import subprocess
from html import unescape

import pytest

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render


@pytest.mark.parametrize('slug,title', [
    ('network-io', '虚拟网卡：收发路径、缓冲区与后端'),
    ('qemu-vm-startup', 'QEMU 虚拟机启动'),
    ('libvirt-live-migration', 'Libvirt 热迁移流程'),
])
def test_renamed_articles_keep_their_urls_and_match_directory(slug, title):
    assert mechanisms.get_article(slug)['title'] == title
    assert title in render.render_mechanisms_html(Config(), slug)
    assert title in render.render_mechanisms_html(Config())


@pytest.mark.parametrize('slug', ['qemu-postcopy', 'vcpu-numa-placement'])
def test_removed_articles_have_no_catalogue_links_or_exclusive_assets(slug):
    assert mechanisms.get_article(slug) is None
    assert mechanisms.get_redirect(slug) is None
    for article in mechanisms.articles():
        page = render.render_mechanisms_html(Config(), article['slug'])
        assert f'{slug}.html' not in page
    assert 'postcopy.js' not in render.MECHANISM_ASSET_NAMES


def test_hotplug_overview_covers_three_resource_lifecycles():
    page = render.render_mechanisms_html(Config(), 'hotplug')
    example = page.split('<section id="example"', 1)[1].split('</section>', 1)[0]
    for term in ('net1', 'query-hotpluggable-cpus', 'query-cpus-fast', 'BSP',
                 'pc-dimm', '_EJ0', 'DEVICE_DELETED', 'online', 'offline'):
        assert term in example
    assert 'href="x86-vcpu-hotplug.html"' in example
    assert '<section id="reading"' not in page


def test_qemu_startup_prepares_whole_machine_before_first_execution():
    page = render.render_mechanisms_html(Config(), 'qemu-vm-startup')
    devices = page.split('<section id="devices"', 1)[1].split('</section>', 1)[0]
    for term in ('RAM', 'ROM', 'BlockBackend', 'TAP', 'MSI-X', 'ACPI', 'fw_cfg',
                 'KVM_CREATE_IRQCHIP', 'Guest 驱动', '磁盘驱动'):
        assert term in devices
    assert page.index('id="devices"') < page.index('id="first-run"')


def test_kvm_creation_covers_objects_and_dependency_order():
    article = mechanisms.get_article('kvm-vm-creation')
    assert article['topic'] == 'kvm-internals'
    page = render.render_mechanisms_html(Config(), article['slug'])
    for term in ('KVM_CREATE_VM', 'KVM_CREATE_VCPU', 'KVM_SET_USER_MEMORY_REGION',
                 'KVM_GET_VCPU_MMAP_SIZE', 'KVM_CREATE_IRQCHIP', 'KVM_RUN',
                 'KVM_SET_CPUID2', 'KVM_SET_SREGS', 'KVM_SET_MP_STATE',
                 '须在创建 vCPU 之前', '宿主线程由 QEMU 创建', '首次进入 Guest'):
        assert term in page
    assert '不是一次性分配全部物理页' in page
    assert '完整寄存器状态使用专门的 vCPU ioctl' in page


def test_upgrade_has_its_own_overview_and_explicit_vfio_scope():
    topic = mechanisms.get_topic('live-upgrade')
    assert topic['overview_article']['slug'] == 'live-upgrade-overview'
    assert {a['slug'] for a in topic['detail_articles']} == {
        'qemu-cpr-transfer', 'kvm-live-upgrade',
    }
    page = render.render_mechanisms_html(Config(), 'live-upgrade-overview')
    for term in ('CPR-transfer', 'Dual KVM', 'IOMMUFD', 'DMA', '暂停窗口',
                 '不等于可以在线替换任意 vfio-pci', '新实例已修改共享 RAM'):
        assert term in page
    assert len(re.findall(r'href="[a-z-]+\.html"', page)) == 2


def test_linux_boot_follows_firmware_kernel_and_userspace_handoffs():
    page = render.render_mechanisms_html(Config(), 'linux-boot')
    for term in ('BIOS', 'UEFI', 'memblock', 'start_kernel()', 'rest_init()',
                 'kthreadd', 'initramfs', '/init', 'switch_root', 'PID 1',
                 'systemd', 'critical-chain', '内建驱动', '模块驱动'):
        assert term in page
    assert 'PID 1 此时仍执行内核代码' in page
    assert '同一个 PID 1 换入新的程序映像' in page
    sections = [s['id'] for s in mechanisms.get_article('linux-boot')['sections']]
    assert sections == ['preface', 'firmware', 'early', 'kernel', 'devices',
                        'root', 'services', 'observation', 'sources']


def test_overviews_do_not_repeat_directory_as_reading_lists():
    for slug in ('virtualization-basics', 'libvirt-internals', 'qemu-internals',
                 'device-virtualization', 'migration', 'hotplug'):
        page = render.render_mechanisms_html(Config(), slug)
        assert '<section id="reading"' not in page
        assert 'id="sources"' in page


def test_interrupt_consolidation_preserves_subsection_bookmarks_and_order():
    article = mechanisms.get_article('interrupt-virtualization')
    page = render.render_mechanisms_html(Config(), article['slug'])
    for old, target in article.get('anchor_aliases', {}).items():
        assert f'id="{old}"' in page
        assert f'id="{target}"' in page
    for identifier in ('msix', 'structures', 'comparison'):
        assert f'<h3 id="{identifier}">' in page
        assert f'<section id="{identifier}"' not in page
    assert page.index('id="notification"') < page.index('id="delivery"')
    assert page.index('id="delivery"') < page.index('id="acceleration"')
    assert page.index('id="acceleration"') < page.index('id="walkthrough"')


def test_migration_example_is_shell_syntax_only_and_has_domain_and_destination(article_source):
    source = article_source('libvirt-live-migration')
    shell = unescape(re.findall(r'<pre><code>(.*?)</code></pre>', source, re.S)[0])
    result = subprocess.run(['bash', '-n'], input=shell, text=True,
                            capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert 'demo qemu+ssh://host-b/system' in shell
    assert 'qemu+tcp://host-b/system' in source
    assert 'postcopy' not in source.lower()
    assert 'TCP URI 本身不承诺加密' in source
