"""Editorial reading order, published-only navigation, and cross-topic links."""
import copy
import re

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render


def test_directory_metadata_covers_each_topic_and_article_once():
    content = mechanisms._content()
    group_slugs = [group['slug'] for group in content['groups']]
    topic_slugs = [topic['slug'] for topic in content['topics']]
    assert len(set(group_slugs)) == len(group_slugs)
    assert len(set(topic_slugs)) == len(topic_slugs)
    for topic in content['topics']:
        assert topic['group'] in group_slugs
        overview = mechanisms.get_article(topic['overview'])
        assert overview is not None and overview['topic'] == topic['slug']
    groups = mechanisms.topic_groups()
    assert [topic['slug'] for group in groups for topic in group['topics']] == topic_slugs
    members = [article['slug'] for group in groups for topic in group['published_topics']
               for article in topic['articles']]
    assert sorted(members) == sorted(article['slug'] for article in mechanisms.articles())


def test_directory_has_reading_order_and_real_anchors_only():
    page = render.render_mechanisms_html(Config())
    directory = re.search(r'<nav class="mechanism-directory".*?</nav>', page, re.S).group()
    assert page.index('结合源码与图解') < page.index('class="mechanism-directory"')
    assert re.findall(r'href="#([^"]+)"', directory) == [
        'virtualization-basics',
        'cpu-virtualization', 'memory-virtualization', 'device-virtualization',
        'interrupt-virtualization', 'vm-startup', 'migration', 'hotplug',
        'live-upgrade', 'qemu-internals', 'kvm-internals', 'libvirt-internals',
    ]
    assert re.findall(r'<section class="mechanism-topic" id="([^"]+)"', page) == [
        'virtualization-basics',
        'cpu-virtualization', 'memory-virtualization', 'device-virtualization',
        'interrupt-virtualization', 'vm-startup', 'migration', 'hotplug',
        'live-upgrade', 'qemu-internals', 'kvm-internals', 'libvirt-internals',
    ]
    for slug in re.findall(r'href="#([^"]+)"', directory):
        assert f'id="{slug}"' in page
    assert '待补充' not in page
    assert 'mechanism-directory-planned' not in page
    assert 'id="group-internals"' in page
    assert 'disabled' not in directory


def test_directory_counts_primary_articles_without_extra_related_rows():
    counts = {topic['slug']: len(topic['articles']) for topic in mechanisms.topics()}
    assert counts == {'virtualization-basics': 1, 'cpu-virtualization': 6, 'memory-virtualization': 5,
                      'device-virtualization': 11, 'interrupt-virtualization': 2,
                      'vm-startup': 3, 'migration': 6, 'hotplug': 4,
                      'live-upgrade': 2, 'qemu-internals': 6, 'libvirt-internals': 2,
                      'kvm-internals': 2}
    assert [article['slug'] for article in
            mechanisms.get_topic('memory-virtualization')['articles']] == [
                'memory-virtualization', 'kvm-ept-fault', 'kvm-hugepages', 'kvm-dirty-ring',
                'vcpu-numa-placement',
            ]
    page = render.render_mechanisms_html(Config())
    migration = re.search(r'<section class="mechanism-topic" id="migration".*?</section>',
                          page, re.S).group()
    assert '相关原理' not in page
    assert 'mechanism-topic-related' not in page
    assert 'href="mechanisms/kvm-dirty-ring.html"' not in migration
    assert page.count('href="mechanisms/kvm-dirty-ring.html"') == 1
    assert '内存虚拟化</a>' in render.render_mechanisms_html(Config(), 'kvm-dirty-ring')
    assert '设备与 I/O 虚拟化</a>' in render.render_mechanisms_html(Config(), 'qemu-vhost-net')


def test_merged_io_topic_keeps_legacy_anchor_without_duplicate_articles():
    canonical = mechanisms.get_topic('device-virtualization')
    assert mechanisms.get_topic('io-virtualization') is canonical
    assert [article['slug'] for article in canonical['articles']] == [
        'device-virtualization',
        'virtio-device-initialization',
        'qemu-network-tx', 'qemu-vhost-net', 'dpdk-vhost-user-net', 'spdk-vhost-user-blk', 'qemu-vfio-dma',
        'qemu-virtio-blk', 'virtio-net-rx', 'packed-virtqueue', 'vhost-vdpa',
    ]
    page = render.render_mechanisms_html(Config())
    assert page.count('id="io-virtualization"') == 1
    assert 'href="#io-virtualization"' not in page
    assert page.index('id="device-virtualization"') < page.index('id="io-virtualization"')


def test_planned_topic_becomes_published_without_manual_counts(monkeypatch):
    metadata = {
        'groups': [{'slug': 'internals', 'title': '内部机制'}],
        'topics': [{'slug': 'qemu-internals', 'title': 'QEMU 内部机制', 'group': 'internals'}],
        'articles': [],
    }
    monkeypatch.setattr(mechanisms, '_content', lambda: metadata)
    mechanisms.topics.cache_clear()
    try:
        group = mechanisms.topic_groups()[0]
        assert not group['published_topics']
        assert not group['topics'][0]['articles']
        metadata['articles'].append({'slug': 'qom', 'topic': 'qemu-internals'})
        mechanisms.topics.cache_clear()
        group = mechanisms.topic_groups()[0]
        assert group['published_topics'][0]['articles'][0]['slug'] == 'qom'
        assert 'articles' not in metadata['topics'][0]
    finally:
        mechanisms.topics.cache_clear()


def test_group_building_does_not_mutate_editorial_metadata():
    original = copy.deepcopy(mechanisms._content())
    mechanisms.topic_groups()
    assert mechanisms._content() == original
