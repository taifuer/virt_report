"""Editorial reading order, published-only navigation, and cross-topic links."""
import copy
import re

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.execution_demos import get_demo
from virt_report.render import render


def test_embedded_demos_belong_to_canonical_articles():
    for slug, demo in (
        ("gpu-execution", "gpu-execution"),
        ("virtio-gpu", "virtio-gpu-rendering"),
        ("interrupt-virtualization", "interrupt-delivery"),
    ):
        article = mechanisms.get_article(slug)
        assert article["execution_demo"] == demo
        assert get_demo(demo)
        assert article.get("presentation") != "walkthrough"
        page = render.render_mechanisms_html(Config(), slug)
        assert f"<title>{article['title']} - virt-report</title>" in page
        assert 'id="execution-demo-data"' in page


def test_directory_metadata_covers_each_topic_and_article_once():
    content = mechanisms._content()
    group_slugs = [group['slug'] for group in content['groups']]
    topic_slugs = [topic['slug'] for topic in content['topics']]
    assert len(set(group_slugs)) == len(group_slugs)
    assert len(set(topic_slugs)) == len(topic_slugs)
    for topic in content['topics']:
        assert topic['group'] in group_slugs
        if topic.get('overview'):
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
    topics = [
        'virtualization-basics', 'libvirt-internals', 'qemu-internals', 'kvm-internals',
        'cpu-virtualization', 'memory-virtualization', 'device-virtualization',
        'interrupt-virtualization', 'vm-startup', 'migration', 'hotplug',
        'live-upgrade', 'linux-foundations',
    ]
    assert re.findall(r'href="([^"]+)"', directory) == ['#' + slug for slug in topics]
    assert re.findall(r'<section class="mechanism-topic" id="([^"]+)"', page) == topics
    for slug in re.findall(r'href="#([^"]+)"', directory):
        assert f'id="{slug}"' in page
    assert '待补充' not in page
    assert 'mechanism-directory-planned' not in page
    assert [group['slug'] for group in mechanisms.topic_groups()] == [
        'architecture', 'mechanisms', 'operations', 'linux',
    ]
    assert 'disabled' not in directory


def test_directory_counts_primary_articles_without_extra_related_rows():
    counts = {topic['slug']: len(topic['articles']) for topic in mechanisms.topics()}
    assert counts == {'linux-foundations': 7, 'virtualization-basics': 1,
                      'cpu-virtualization': 2, 'memory-virtualization': 2,
                      'device-virtualization': 15, 'interrupt-virtualization': 1,
                      'vm-startup': 2, 'migration': 6, 'hotplug': 3,
                      'live-upgrade': 3, 'qemu-internals': 5, 'libvirt-internals': 2,
                      'kvm-internals': 9}
    assert [article['slug'] for article in
            mechanisms.get_topic('memory-virtualization')['articles']] == [
                'memory-virtualization', 'virtio-balloon',
            ]
    page = render.render_mechanisms_html(Config())
    migration = re.search(r'<section class="mechanism-topic" id="migration".*?</section>',
                          page, re.S).group()
    assert '相关原理' not in page
    assert 'mechanism-topic-related' not in page
    assert 'href="mechanisms/kvm-dirty-ring.html"' not in migration
    assert page.count('href="mechanisms/kvm-dirty-ring.html"') == 1
    assert 'href="../mechanisms.html#kvm-internals">KVM 原理</a>' in render.render_mechanisms_html(Config(), 'kvm-dirty-ring')
    assert '设备与 I/O 虚拟化</a>' in render.render_mechanisms_html(Config(), 'qemu-vhost-net')


def test_merged_io_topic_keeps_legacy_anchor_without_duplicate_articles():
    canonical = mechanisms.get_topic('device-virtualization')
    assert mechanisms.get_topic('io-virtualization') is canonical
    assert [article['slug'] for article in canonical['articles']] == [
        'device-virtualization',
        'virtio-device-initialization',
        'qemu-vhost-net', 'dpdk-vhost-user-net', 'spdk-vhost-user-blk', 'qemu-vfio-dma',
        'iommu-dma', 'iommufd', 'qemu-viommu', 'virtio-gpu',
        'network-io', 'gpu-execution', 'qemu-virtio-blk', 'packed-virtqueue', 'vhost-vdpa',
    ]
    page = render.render_mechanisms_html(Config())
    assert page.count('id="io-virtualization"') == 1
    assert 'href="#io-virtualization"' not in page
    assert page.index('id="device-virtualization"') < page.index('id="io-virtualization"')


def test_directory_chip_counts_include_overviews_and_exclude_redirects():
    page = render.render_mechanisms_html(Config())
    directory = re.search(r'<nav class="mechanism-directory".*?</nav>', page, re.S).group()
    counts = dict(re.findall(r'<a href="#([^"]+)"><span>[^<]+</span>'
                             r'<span class="mechanism-directory-count">(\d+)'
                             r'<span class="sr-only"> 篇文章</span></span></a>', directory))
    assert counts == {topic['slug']: str(len(topic['articles'])) for topic in mechanisms.topics()}
    assert sum(int(count) for count in counts.values()) == len(mechanisms.articles())
    assert 'block-operations' not in page
    assert '快照与备份' not in page


def test_kvm_implementation_articles_have_one_component_home():
    cpu = {'kvm-vm-creation', 'kvm-vcpu-requests', 'kvm-vcpu-halt', 'kvm-virtual-time', 'kvm-nested-vmx'}
    memory = {'kvm-ept-fault', 'kvm-hugepages', 'kvm-dirty-ring'}
    topic = mechanisms.get_topic('kvm-internals')
    assert topic['overview_article']['slug'] == 'kvm-vcpu-run'
    assert [(cluster['title'], {article['slug'] for article in cluster['articles']})
            for cluster in topic['article_clusters']] == [('执行与协作', cpu), ('内存管理', memory)]
    for slug in cpu | memory:
        assert mechanisms.get_article(slug)['topic'] == 'kvm-internals'
        page = render.render_mechanisms_html(Config(), slug)
        assert 'href="../mechanisms.html#kvm-internals">KVM 原理</a>' in page
    assert [a['slug'] for a in mechanisms.get_topic('cpu-virtualization')['articles']] == [
        'cpu-virtualization', 'multi-vcpu-coordination',
    ]


def test_walkthrough_category_bookmarks_resolve_to_the_parent_mechanism():
    topic = mechanisms.get_topic('block-io-walkthrough')
    assert topic['slug'] == 'device-virtualization'
    assert topic['group'] == 'mechanisms'
    assert all(article.get('presentation') != 'walkthrough' for article in mechanisms.articles())
    page = render.render_mechanisms_html(Config())
    assert page.count('href="mechanisms/qemu-virtio-blk.html"') == 1
    assert 'id="group-walkthroughs"' not in page
    assert page.count('id="block-io-walkthrough"') == 1
    device = re.search(r'<section class="mechanism-topic" id="device-virtualization".*?</section>',
                       page, re.S).group()
    assert 'virtio-blk-read.html' not in device
    assert 'qemu-virtio-blk.html' in device


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


def test_article_lists_are_progressively_collapsible_without_hiding_links_from_no_js():
    page = render.render_mechanisms_html(Config())
    assert 'assets/mechanism-directory.js?v=' in page
    assert 'mechanism-directory.js' in render.MECHANISM_ASSET_NAMES
    topics = mechanisms.topics()
    assert page.count('data-mechanism-toggle ') == sum(
        bool(topic['detail_articles']) and topic['group'] != 'walkthroughs' for topic in topics
    )
    for topic in topics:
        body = re.search(rf'<section class="mechanism-topic" id="{topic["slug"]}".*?</section>',
                         page, re.S).group()
        assert f'id="mechanism-list-{topic["slug"]}"' in body
        assert re.search(r'<div class="mechanism-list"[^>]*>', body).group().find('hidden') == -1
        if topic.get('overview'):
            assert f'href="mechanisms/{topic["overview"]}.html"' in body
        else:
            assert topic['detail_articles'] == topic['articles']
            heading = re.search(r'<h3[^>]*>(.*?)</h3>', body, re.S).group(1)
            assert '<a ' not in heading
            assert 'class="mechanism-topic-label"' in heading
            assert topic['title'] in heading
        if topic['detail_articles'] and topic['group'] != 'walkthroughs':
            button = re.search(r'<button[^>]*data-mechanism-toggle[^>]*>', body).group()
            assert f'aria-controls="mechanism-list-{topic["slug"]}"' in button
            assert 'aria-expanded="true"' in button
            assert ' hidden' in button
            assert f'data-article-count="{len(topic["detail_articles"])}"' in button
            contents = re.search(r'<button[^>]*data-mechanism-toggle[^>]*>(.*?)</button>',
                                 body, re.S).group(1)
            assert '<svg ' in contents and 'aria-hidden="true"' in contents
            assert '展开' not in contents and '收起' not in contents and '篇' not in contents
            assert 'aria-label=' in button
        else:
            assert 'data-mechanism-toggle' not in body


def test_directory_script_is_not_loaded_on_article_pages():
    page = render.render_mechanisms_html(Config(), 'cpu-virtualization')
    assert 'src="../assets/mechanism-directory.js' not in page


def test_tcg_is_classified_under_qemu_in_both_catalogue_and_article():
    assert mechanisms.get_article('qemu-tcg')['topic'] == 'qemu-internals'
    page = render.render_mechanisms_html(Config())
    qemu = re.search(r'<section class="mechanism-topic" id="qemu-internals".*?</section>',
                     page, re.S).group()
    cpu = re.search(r'<section class="mechanism-topic" id="cpu-virtualization".*?</section>',
                    page, re.S).group()
    assert 'href="mechanisms/qemu-tcg.html"' in qemu
    assert 'qemu-tcg.html' not in cpu
    article = render.render_mechanisms_html(Config(), 'qemu-tcg')
    assert 'href="../mechanisms.html#qemu-internals">QEMU 原理</a>' in article
