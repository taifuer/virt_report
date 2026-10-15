"""Keep introductory reading paths and navigation presentation intact."""
import re

import pytest

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render


@pytest.mark.parametrize('slug,sequence', [
    ('virtualization-basics', ['preface', 'isolation', 'architecture', 'example', 'evolution', 'tradeoffs']),
    ('cpu-virtualization', ['preface', 'paths', 'example', 'evolution', 'evaluation']),
    ('memory-virtualization', ['preface', 'addresses', 'evolution', 'access', 'evaluation']),
    ('device-virtualization', ['preface', 'architecture', 'queue', 'example', 'backends', 'evolution', 'evaluation']),
    ('interrupt-virtualization', ['preface', 'controllers', 'path', 'acceleration', 'example', 'evolution', 'evaluation']),
])
def test_foundation_guides_introduce_principles_before_examples_and_tradeoffs(slug, sequence):
    article = mechanisms.get_article(slug)
    section_ids = [section['id'] for section in article['sections']]
    assert section_ids == [*sequence, 'reading', 'sources']
    page = render.render_mechanisms_html(Config(), slug)
    positions = [page.index(f'<section id="{section}"') for section in sequence]
    assert positions == sorted(positions)
    assert page.count('data-mechanism-view="diagram"') >= 1
    assert '<table>' in page


def test_interrupt_guide_compares_distinct_layers_and_preserves_previous_anchors():
    page = render.render_mechanisms_html(Config(), 'interrupt-virtualization')
    for name in ('interrupt-controller-paths', 'interrupt-acceleration-layers',
                 'interrupt-delivery-overview'):
        assert f'data-diagram="{name}"' in page
    for anchor in ('preface', 'path', 'example', 'evolution', 'evaluation', 'reading', 'sources'):
        assert f'id="{anchor}"' in page
    for concept in ('PIC', 'I/O APIC', 'MSI-X', 'in-kernel irqchip', 'split irqchip',
                    'irqfd', 'APICv', 'AVIC', 'VT-d', 'IRR', 'EOI', 'NAPI'):
        assert concept in page
    assert 'lwn.net/Articles/329837/' in page


def test_directory_links_do_not_underline_but_keep_focus_feedback():
    css = (render.ASSETS_DIR / 'mechanisms.css').read_text()
    normal = re.search(r'\.mechanism-directory a\{background:[^}]+\}', css).group()
    interactive = re.search(
        r'\.mechanism-directory a:hover,\.mechanism-directory a:focus-visible\{[^}]+\}', css).group()
    assert 'text-decoration:none' in normal
    assert 'text-decoration:none' in interactive
    assert 'background:var(--brand-soft)' in interactive
    assert '.mechanisms-index a:focus-visible{outline:2px solid var(--brand)' in css


@pytest.mark.parametrize('slug', ['vm-startup', 'migration', 'hotplug', 'live-upgrade'])
def test_operation_guides_explain_state_before_handoff_and_evaluate_outcomes(slug):
    article = mechanisms.get_article(slug)
    assert [section['id'] for section in article['sections']] == [
        'preface', 'state', 'process', 'example', 'evolution', 'evaluation', 'reading', 'sources',
    ]
    page = render.render_mechanisms_html(Config(), slug)
    for section in ('state', 'example', 'evaluation'):
        body = re.search(fr'<section id="{section}".*?</section>', page, re.S).group()
        assert '<p>' in body
    assert '<table>' in page


@pytest.mark.parametrize('slug,diagram,example_fields', [
    ('libvirt-internals', 'libvirt-suspend-sequence', ('suspend demo', 'STOP', 'DEVICE_DELETED')),
    ('qemu-internals', 'qemu-overview-read-lifecycle', ('sector = 2048', 'len: 4097', 'IOThread')),
    ('kvm-internals', 'kvm-internals-execution', ('0x10', 'KVM_EXIT_MMIO', 'kvm_run')),
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


def test_device_overview_introduces_negotiation_before_the_queue_example():
    page = render.render_mechanisms_html(Config(), 'device-virtualization')
    start = page.index('id="queue"')
    end = page.index('id="example"')
    initialization = page[start:end]
    assert initialization.index('FEATURES_OK') < initialization.index('DRIVER_OK')
    assert '读回确认' in initialization


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
