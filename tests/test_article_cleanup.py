"""Consolidation keeps technical content, canonical search and old bookmarks."""
import re

from virt_report import editorial_search, mechanisms
from virt_report.config import Config
from virt_report.render import render


def test_merged_overviews_are_not_independent_search_results():
    documents = editorial_search._editorial_documents()
    articles = {item['slug'] for item in documents if item['kind'] == 'articles'}
    assert len(articles) == 56
    assert not {'migration', 'virtio-balloon'} & articles
    assert mechanisms.get_topic('migration')['overview'] == 'qemu-memory-migration'
    assert mechanisms.get_topic('device-virtualization')['overview_article']['slug'] == 'device-virtualization'
    for template in ('_migration_overview_article.html', '_device_overview_article.html'):
        assert not (render.TEMPLATES_DIR / template).exists()


def test_device_overview_follows_a_packet_and_a_block_read():
    slug = 'device-virtualization'
    article = mechanisms.get_article(slug)
    assert article['title'] == '设备与 I/O 虚拟化原理'
    assert [section['id'] for section in article['sections']] == [
        'preface', 'architecture', 'example', 'queue', 'dma', 'storage', 'evaluation',
        'summary', 'sources',
    ]
    page = render.render_mechanisms_html(Config(), slug)
    assert '<h1>设备与 I/O 虚拟化原理</h1>' in page
    for term in ('e1000', 'TDT', 'vhost-net', 'vhost-user', 'vDPA', 'SR-IOV',
                 'IOMMU', 'VIRTIO_BLK_T_IN', '2048 至 2055', '状态字节'):
        assert term in page
    assert all(term in page for term in ('callfd', 'GPA', 'IOVA', 'VIRTIO_BLK_T_FLUSH', '在途'))
    assert '<table' in page.split('<section id="preface"', 1)[1].split('</section>', 1)[0]
    assert set(article['anchor_aliases']) <= set(re.findall(r'\bid="([^"]+)"', page))
    assert 'article-redirect.js' not in page and 'data-execution-lab' not in page


def test_qemu_migration_is_a_complete_flow_with_iteration_and_device_state():
    article = mechanisms.get_article('qemu-memory-migration')
    assert article['title'] == 'QEMU 热迁移流程'
    assert [section['id'] for section in article['sections']] == [
        'preface', 'state', 'principle', 'example', 'iteration', 'convergence',
        'downtime', 'implementation', 'optimization', 'summary', 'sources',
    ]
    page = render.render_mechanisms_html(Config(), article['slug'])
    for text in ('incoming', '共享存储', 'used ring', 'drain', 'bmap',
                 'dirty-pages-rate', 'threshold_size', 'data-migration-interactive'):
        assert text in page
    assert page.index('id="example"') < page.index('id="iteration"')


def test_upgrade_overview_compares_scopes_without_repeating_two_implementations():
    article = mechanisms.get_article('live-upgrade-overview')
    assert [section['id'] for section in article['sections']] == [
        'preface', 'scope', 'handoff', 'vfio', 'evaluation', 'sources',
    ]
    page = render.render_mechanisms_html(Config(), article['slug'])
    for old in ('qemu', 'kvm'):
        assert f'id="{old}"' in page
        assert f'<section id="{old}"' not in page
    for slug in ('qemu-cpr-transfer', 'kvm-live-upgrade'):
        assert len(re.findall(fr'href="{slug}\.html"', page)) == 1
    assert 'IOMMUFD' in page and '新实例已修改共享 RAM' in page
