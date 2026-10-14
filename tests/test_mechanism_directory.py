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
        for slug in topic.get('related_articles', ()):
            assert mechanisms.get_article(slug) is not None
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
        'cpu-virtualization', 'memory-virtualization', 'device-virtualization',
        'interrupt-virtualization', 'vm-startup', 'migration', 'hotplug',
        'live-upgrade', 'qemu-internals', 'libvirt-internals',
    ]
    assert re.findall(r'<section class="mechanism-topic" id="([^"]+)"', page) == [
        'cpu-virtualization', 'memory-virtualization', 'device-virtualization',
        'interrupt-virtualization', 'vm-startup', 'migration', 'hotplug',
        'live-upgrade', 'qemu-internals', 'libvirt-internals',
    ]
    for slug in re.findall(r'href="#([^"]+)"', directory):
        assert f'id="{slug}"' in page
    assert '待补充' not in page
    assert 'mechanism-directory-planned' not in page
    assert 'id="group-internals"' in page
    assert 'disabled' not in directory


def test_directory_counts_primary_articles_and_keeps_migration_dirty_ring_link():
    counts = {topic['slug']: len(topic['articles']) for topic in mechanisms.topics()}
    assert counts == {'cpu-virtualization': 3, 'memory-virtualization': 3,
                      'device-virtualization': 5, 'interrupt-virtualization': 1,
                      'vm-startup': 2, 'migration': 4, 'hotplug': 2,
                      'live-upgrade': 1, 'qemu-internals': 4, 'libvirt-internals': 1}
    assert [article['slug'] for article in
            mechanisms.get_topic('memory-virtualization')['articles']] == [
                'kvm-ept-fault', 'kvm-hugepages', 'kvm-dirty-ring',
            ]
    page = render.render_mechanisms_html(Config())
    migration = re.search(r'<section class="mechanism-topic" id="migration".*?</section>',
                          page, re.S).group()
    assert '相关原理' in migration
    assert 'href="mechanisms/kvm-dirty-ring.html"' in migration
    assert '<h4><a href="mechanisms/kvm-dirty-ring.html"' not in migration
    assert '内存虚拟化</a>' in render.render_mechanisms_html(Config(), 'kvm-dirty-ring')
    assert '设备与 I/O 虚拟化</a>' in render.render_mechanisms_html(Config(), 'qemu-vhost-net')


def test_merged_io_topic_keeps_legacy_anchor_without_duplicate_articles():
    canonical = mechanisms.get_topic('device-virtualization')
    assert mechanisms.get_topic('io-virtualization') is canonical
    assert [article['slug'] for article in canonical['articles']] == [
        'qemu-network-tx', 'qemu-vhost-net', 'spdk-vhost-user-blk', 'qemu-vfio-dma',
        'qemu-virtio-blk',
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
