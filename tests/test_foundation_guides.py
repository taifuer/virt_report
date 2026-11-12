"""Keep introductory reading paths and navigation presentation intact."""
import re

import pytest

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render


@pytest.mark.parametrize('slug,sequence', [
    ('virtualization-basics', ['preface', 'isolation', 'architecture', 'example', 'evolution', 'tradeoffs']),
    ('cpu-virtualization', ['preface', 'paths', 'walkthrough', 'comparison', 'evolution', 'evaluation']),
    ('memory-virtualization', ['preface', 'addresses', 'evolution', 'access', 'walkthrough',
                               'address-routing', 'comparison', 'evaluation', 'verification', 'experiment']),
    ('device-virtualization', ['preface', 'example', 'architecture', 'queue', 'backends', 'evolution', 'evaluation']),
    ('interrupt-virtualization', ['preface', 'example', 'controllers', 'notification',
                                  'delivery', 'irqfd', 'acceleration', 'walkthrough',
                                  'implementation', 'evolution', 'evaluation']),
])
def test_foundation_guides_introduce_principles_before_examples_and_tradeoffs(slug, sequence):
    article = mechanisms.get_article(slug)
    section_ids = [section['id'] for section in article['sections']]
    tail = ['sources']
    assert section_ids == [*sequence, *tail]
    page = render.render_mechanisms_html(Config(), slug)
    positions = [page.index(f'<section id="{section}"') for section in sequence]
    assert positions == sorted(positions)
    assert page.count('data-mechanism-view="diagram"') >= 1
    assert '<table>' in page


def test_interrupt_guide_compares_distinct_layers_and_preserves_previous_anchors(article_source):
    page = render.render_mechanisms_html(Config(), 'interrupt-virtualization')
    for name in ('interrupt-controller-paths', 'interrupt-acceleration-layers', 'interrupt-software-delivery',
                 'interrupt-delivery-overview'):
        assert f'data-diagram="{name}"' in page
    for anchor in ('preface', 'path', 'example', 'evolution', 'evaluation', 'reading', 'sources'):
        assert f'id="{anchor}"' in page
    for concept in ('PIC', 'I/O APIC', 'MSI-X', 'in-kernel irqchip', 'split irqchip',
                    'irqfd', 'APICv', 'AVIC', 'VT-d', 'IRR', 'EOI', 'NAPI'):
        assert concept in page
    assert 'lwn.net/Articles/329837/' in article_source('interrupt-virtualization')


def test_interrupt_guide_traces_software_delivery_before_acceleration():
    page = render.render_mechanisms_html(Config(), 'interrupt-virtualization')
    baseline = page[page.index('<section id="notification"'):page.index('<section id="walkthrough"')]
    stages = ('id="notification"', 'id="msix"', 'id="delivery"', 'id="irqfd"')
    assert [baseline.index(stage) for stage in stages] == sorted(
        baseline.index(stage) for stage in stages)
    for concept in ('KVM_SIGNAL_MSI', 'IRR[0x51]', 'ISR[0x51]', 'IDT', 'EOI'):
        assert concept in baseline
    assert page.index('data-diagram="interrupt-software-delivery"') < page.index('id="acceleration"')
    # Message semantics and host event delivery must not be taught as one layer.
    assert 'MSI-X / irqfd 路由' not in page
    assert '现在把收包处理从 QEMU 切换到 vhost-net' in page
    assert '换成 VFIO 分配的物理设备后' in page
    source = (render.ASSETS_DIR.parents[1] / 'content/diagrams'
              / 'interrupt-software-delivery.mmd').read_text()
    assert source.index('Linux 边沿应答') < source.index('V->>K: EOI') < source.index('驱动回调 → NAPI')


@pytest.mark.parametrize('slug,sequence', [
    ('kvm-vcpu-run', ['paths', 'structures', 'completion', 'notifications']),
    ('interrupt-virtualization', ['notification', 'msix', 'delivery', 'structures', 'irqfd']),
    ('libvirt-rpc', ['layers', 'framing', 'dispatch', 'responses']),
])
def test_request_walkthrough_finishes_baseline_before_comparisons(slug, sequence):
    page = render.render_mechanisms_html(Config(), slug)
    # Related phases may be subsections while keeping stable deep links.
    offsets = [page.index(f'id="{section}"') for section in sequence]
    assert offsets == sorted(offsets)


def test_directory_links_do_not_underline_but_keep_focus_feedback():
    css = (render.ASSETS_DIR / 'mechanisms.css').read_text()
    normal = re.search(r'\.mechanism-directory a\{background:[^}]+\}', css).group()
    interactive = re.search(
        r'\.mechanism-directory a:hover,\.mechanism-directory a:focus-visible\{[^}]+\}', css).group()
    assert 'text-decoration:none' in normal
    assert 'text-decoration:none' in interactive
    assert 'background:var(--brand-soft)' in interactive
    assert '.mechanisms-index a:focus-visible{outline:2px solid var(--brand)' in css


@pytest.mark.parametrize('slug', ['migration', 'hotplug'])
def test_operation_guides_explain_state_before_handoff_and_evaluate_outcomes(slug):
    article = mechanisms.get_article(slug)
    assert [section['id'] for section in article['sections']] == [
        'preface', 'state', 'process', 'example', 'evolution', 'evaluation', 'sources',
    ]
    page = render.render_mechanisms_html(Config(), slug)
    for section in ('state', 'example', 'evaluation'):
        body = re.search(fr'<section id="{section}".*?</section>', page, re.S).group()
        assert '<p>' in body
    assert '<table>' in page


@pytest.mark.parametrize('slug,diagram,example_fields', [
    ('libvirt-internals', 'libvirt-suspend-sequence', ('suspend demo', 'STOP', 'DEVICE_DELETED')),
    ('qemu-internals', 'qemu-overview-read-lifecycle', ('4 KiB', 'used', 'IOThread')),
    ('kvm-vcpu-run', 'kvm-run-exits', ('0xf0001000', 'KVM_EXIT_MMIO', 'kvm_run')),
])
def test_component_guides_have_local_example_diagrams_and_state_handoffs(slug, diagram, example_fields):
    page = render.render_mechanisms_html(Config(), slug)
    assert f'data-diagram="{diagram}"' in page
    assert diagram in render.MECHANISM_DIAGRAM_NAMES
    assert (render.ASSETS_DIR / f'{diagram}.svg').is_file()
    for field in example_fields:
        assert field in page
    assert '<table>' in page
    assert 'id="evolution"' in page


def test_device_overview_keeps_negotiation_readable_and_links_to_protocol_detail():
    page = render.render_mechanisms_html(Config(), 'device-virtualization')
    start = page.index('id="queue"')
    end = page.index('id="backends"')
    initialization = page[start:end]
    assert '先确认共同支持的特性和队列格式' in initialization
    assert 'virtio-device-initialization.html' in initialization
    assert 'FEATURES_OK' not in initialization
    detail = render.render_mechanisms_html(Config(), 'virtio-device-initialization')
    assert detail.index('FEATURES_OK') < detail.index('DRIVER_OK')


def test_clock_article_distinguishes_reading_and_timer_delivery():
    page = render.render_mechanisms_html(Config(), 'kvm-virtual-time')
    assert page.index('id="pvclock"') < page.index('id="timer"') < page.index('id="migration"')
    for diagram in ('kvm-clock-read', 'kvm-deadline-timer'):
        assert f'data-diagram="{diagram}"' in page
        assert diagram in render.MECHANISM_DIAGRAM_NAMES
    for field in ('tsc_timestamp', 'system_time', 'tsc_to_system_mul', 'tsc_shift',
                  'clocksource', 'clockevent', 'PVCLOCK_GUEST_STOPPED', '26,000,000'):
        assert field in page
    assert 'kvm-virtual-time.html' in render.render_mechanisms_html(Config(), 'cpu-virtualization')


def test_virtio_initialization_article_keeps_layout_and_reset_boundaries():
    page = render.render_mechanisms_html(Config(), 'virtio-device-initialization')
    assert page.index('id="negotiation"') < page.index('id="queue-layout"') < page.index('id="first-request"')
    for diagram in ('virtio-init-handshake', 'virtio-init-queue-layout'):
        assert f'data-diagram="{diagram}"' in page
        assert diagram in render.MECHANISM_DIAGRAM_NAMES
    for field in ('0x100000', '0x101000', '0x102000', '0x0b', '0x0f',
                  'FEATURES_OK', 'DRIVER_OK', 'queue_enable', 'queue_reset'):
        assert field in page
    assert 'virtio-device-initialization.html' in render.render_mechanisms_html(
        Config(), 'device-virtualization')
