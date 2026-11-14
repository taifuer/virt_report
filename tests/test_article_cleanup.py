"""Consolidation keeps technical content, canonical search and old bookmarks."""
import re

from virt_report import editorial_search, mechanisms
from virt_report.config import Config
from virt_report.render import render


def test_merged_overviews_are_not_independent_search_results():
    documents = editorial_search._editorial_documents()
    articles = {item['slug'] for item in documents if item['kind'] == 'articles'}
    assert len(articles) == 56
    assert not {'migration', 'device-virtualization'} & articles
    assert mechanisms.get_topic('migration')['overview'] == 'qemu-memory-migration'
    assert mechanisms.get_topic('device-virtualization')['overview_article'] is None
    for template in ('_migration_overview_article.html', '_device_overview_article.html'):
        assert not (render.TEMPLATES_DIR / template).exists()


def test_qemu_migration_is_a_complete_flow_with_iteration_and_device_state():
    article = mechanisms.get_article('qemu-memory-migration')
    assert article['title'] == 'QEMU 热迁移流程'
    assert [section['id'] for section in article['sections']] == [
        'preface', 'state', 'principle', 'example', 'iteration', 'convergence',
        'downtime', 'implementation', 'optimization', 'sources',
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
