"""Annual reviews stay source-backed, discoverable and independent of report generation."""
from copy import deepcopy
from datetime import date, datetime, timezone
from html import unescape
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import tomllib

import pytest

from virt_report import kvm_forum, server
from virt_report.config import Config, Render
from virt_report.render import render


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids, self.links = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a':
            self.links.append(attrs.get('href', ''))


def test_event_dates_and_review_publication_are_independent():
    edition = {'starts_on': '2026-11-12', 'ends_on': '2026-11-13',
               'timezone': 'America/Vancouver'}
    for day, expected in ((11, 'upcoming'), (12, 'ongoing'), (13, 'ongoing'), (14, 'completed')):
        assert kvm_forum.event_status(edition, date(2026, 11, day)) == expected
    assert 'review' not in edition
    assert kvm_forum.event_status({'year': 2025}) == 'archived'
    with pytest.raises(ValueError, match='precedes'):
        kvm_forum.event_status({**edition, 'ends_on': '2026-11-11'})


def test_event_progress_uses_venue_date_not_server_date(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return datetime(2026, 11, 14, 0, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(kvm_forum, 'datetime', Clock)
    edition = kvm_forum.load_content()[0][-1]
    assert edition['event_status'] == 'ongoing'  # Still November 13 in Vancouver.
    assert edition['review']  # A date transition never manufactures a review.


def test_completed_event_cannot_show_pre_event_labels():
    editions, analysis = kvm_forum.load_content(date(2026, 11, 15))
    html = render.render_kvm_forum_html(Config(), editions, analysis)
    assert editions[-1]['event_status'] == 'completed'
    assert not any(value in html for value in ('议程预览', '核对于', '尚未召开', '会议进行中'))
    assert 'id="year-2026"' in html
    assert html.index('id="year-2026"') < html.index('id="year-2025"')
    assert html.index('id="forum-trends"') < html.index('class="forum-years"')
    assert 'class="forum-history"' not in html
    assert '<nav class="kicker"' not in html
    assert 'class="forum-actions"' not in html
    assert '2026' in analysis['eras'][-1]['years']
    for edition in editions:
        if edition.get('review'):
            assert f'<h3><a href="{edition["review"]["path"]}">' in html
    assert '>原始议程</a>' in html


@pytest.mark.parametrize('year', [2027, 9999])
def test_unreviewed_years_have_no_annual_review_page(year):
    assert kvm_forum.get_review(year) is None


def test_every_archived_year_has_a_complete_review():
    editions, _ = kvm_forum.load_content()
    historical = [edition for edition in editions if 2010 <= edition['year'] <= 2026]
    assert [edition['year'] for edition in historical] == list(range(2010, 2027))
    assert all(edition.get('review') for edition in historical)
    assert all(5 <= len(edition['review']['talks']) <= 10 for edition in historical)
    assert len({len(edition['review']['talks']) for edition in historical}) > 1
    assert all(edition['titles'] for edition in historical)


def test_editorial_trends_survive_title_analysis_refresh(tmp_path, monkeypatch):
    analysis_path = tmp_path / 'analysis.json'
    monkeypatch.setattr(kvm_forum, 'ANALYSIS_PATH', analysis_path)
    editorial = json.loads(kvm_forum.TRENDS_PATH.read_text())
    for old_headline in ('Legacy summary', 'Refreshed title-only summary'):
        analysis_path.write_text(json.dumps({
            'headline': old_headline, 'overview': 'Only covers old years',
            'eras': [{'years': '2010—2025', 'name': 'Old', 'summary': 'Old'}],
            'years': [{'year': 2026, 'headline': 'Must not replace editorial review'}],
        }))
        editions, analysis = kvm_forum.load_content()
        for key in ('headline', 'overview', 'eras'):
            assert analysis[key] == editorial[key]
        assert '2026' in analysis['overview']
        assert editions[-1]['analysis']['headline'] != 'Must not replace editorial review'


def test_trends_group_recent_years_and_cover_more_than_selected_talks():
    editions, analysis = kvm_forum.load_content()
    assert analysis['headline'] == 'KVM Forum 的技术演进'
    assert [era['years'] for era in analysis['eras']] == [
        '2010—2013', '2014—2017', '2018—2021', '2022—2026',
    ]
    assert '长期并行发展' in analysis['overview']
    recent = analysis['eras'][-1]['summary']
    for topic in ('guest_memfd', 'KVM planes', 'CXL', 'vGPU 上游化',
                  'ARM64', 'CPU 与内存超分', '迁移', '热更新'):
        assert topic in recent
    assert '降低迁移后的缺页代价' not in recent
    html = render.render_kvm_forum_html(Config(), editions, analysis)
    assert html.count('class="era-years"') == 4
    assert html.index('2022—2026') < html.index('2018—2021')


def test_annual_overviews_summarize_the_agenda_not_the_selection():
    editions, analysis = kvm_forum.load_content()
    html = render.render_kvm_forum_html(Config(), editions, analysis)
    paragraphs = re.findall(r'<p class="forum-year-summary">(.*?)</p>', html, flags=re.S)
    assert [unescape(paragraph) for paragraph in paragraphs] == [
        edition['analysis']['summary'] for edition in reversed(editions)
    ]
    for edition in editions:
        annual = edition['analysis']
        assert len(annual['summary']) >= 180, edition['year']
        assert annual['summary'] != edition['review']['summary'], edition['year']
        for text in (annual['headline'], annual['summary'], *annual['themes']):
            assert '精选' not in text and '解读' not in text, edition['year']


@pytest.mark.parametrize('year,topics', [
    (2025, ('NVIDIA vGPU', 'Grace Blackwell', 'Rust', '单一二进制',
            'Windows on Arm', 'RISC-V pKVM', '芯粒')),
    (2026, ('guest_memfd', 'KVM planes', 'SVSM', 'CXL Type-2',
            'ARM64', 'AF_VSOCK', 'Firecracker')),
])
def test_recent_annual_overviews_include_directions_beyond_selected_talks(year, topics):
    edition = kvm_forum.get_review(year)
    assert all(topic in edition['analysis']['summary'] for topic in topics)


def test_selected_review_intro_does_not_replace_the_annual_overview():
    editions, analysis = kvm_forum.load_content()
    original = render.render_kvm_forum_html(Config(), editions, analysis)
    updated = deepcopy(editions)
    edition = next(item for item in updated if item['year'] == 2025)
    edition['review']['summary'] = '用于精选解读页面的独立导语。'
    assert render.render_kvm_forum_html(Config(), updated, analysis) == original
    article = render.render_kvm_forum_review_html(Config(), edition)
    assert f'<p>{edition["review"]["summary"]}</p>' in article
    assert edition['analysis']['summary'] not in unescape(article)


def test_cpu_review_separates_deployment_scenarios_and_evidence():
    html = render.render_kvm_forum_review_html(Config(), kvm_forum.get_review(2026))
    section = html.split('<section id="cpu-overcommit"', 1)[1].split('</section>', 1)[0]
    tables = re.findall(r'<table\b.*?</table>', section, flags=re.S)
    assert len(tables) == 2  # Scenario comparison and separately scoped measurements.
    rows = re.findall(r'<tr\b.*?</tr>', tables[0], flags=re.S)[1:]
    assert len(rows) == 3
    for row, terms in zip(rows, (
        ('专用实例', '1:1', 'MWAIT', '阻塞'),
        ('共享实例', '宿主线程调度', '轻量 vCPU 上下文'),
        ('1:1', '宿主任务', 'sched_ext'),
    )):
        assert all(term in unescape(row) for term in terms)
    for explanation in (
        '宿主可调度的逻辑 CPU', '没有配置 vCPU 数量超分也会遇到',
        '标志', '4 KiB', '64 字节', 'Linux 调度器仍负责承载线程',
        '函数调用式分派', '改用 sched_ext 策略前', '最长约 13 ms', '未给出同负载下启用新策略后的延迟',
    ):
        assert explanation in unescape(section)
    assert section.index('</table>') < section.index('实现挑战')
    assert section.index('同一承载线程内') < section.index('验证结果')


def test_published_reviews_have_original_sources_stable_anchors_and_no_duplicates():
    editions, _ = kvm_forum.load_content()
    for edition in editions:
        if not edition.get('review'):
            continue
        review = edition['review']
        assert 5 <= len(review['talks']) <= 10
        html = render.render_kvm_forum_review_html(Config(), edition)
        parsed = Links(html)
        assert len(parsed.ids) == len(set(parsed.ids))
        assert 'class="forum-review mechanism-article"' in html
        assert 'aria-label="文章目录"' in html
        assert 'href="../conferences.html" class="on" aria-current="page">会议</a>' in html
        assert 'book-sidebar' not in html
        assert 'language-component' not in html and '{#' not in html
        for fragment in (link[1:] for link in parsed.links if link.startswith('#')):
            assert fragment in parsed.ids
        for talk in review['talks']:
            assert talk['id'] in parsed.ids
            assert talk['slides_url'] in parsed.links and talk['url'] in parsed.links
            assert talk['original_title'] in unescape(html)
            assert f'原始讲稿（{talk["slides_format"]}）' in html
            assert '重点页码' not in html
            assert 'class="forum-source-title"' in html
            assert 'class="forum-source-author"' in html
            assert 'class="forum-source-download"' in html
            assert re.search(r'<p class="forum-source-title">[^\n]*class="forum-source-download"[^\n]*</p>', html)
            assert len(talk['summary']) >= 50
            section = html.split(f'<section id="{talk["id"]}"', 1)[1].split('</section>', 1)[0]
            assert section.index('class="forum-talk-source"') < section.index('<h3')
            assert section.count('<h3') >= 5
            assert len(re.sub('<[^>]+>', '', section)) >= 600
            headings = ('问题与工程价值', '实现挑战', '解决方案', '验证结果', '局限与后续工作')
            positions = [section.index(title) for title in headings]
            assert positions == sorted(positions)
        assert '**' not in re.sub('<[^>]+>', '', html)


def test_selection_grid_keeps_substantive_summaries_and_omits_selftests():
    editions, analysis = kvm_forum.load_content()
    html = render.render_kvm_forum_html(Config(), editions, analysis)
    review = kvm_forum.get_review(2026)['review']
    assert 'vfio-selftests' not in {talk['id'] for talk in review['talks']}
    assert '<ol class="forum-selections"' not in html
    assert '<ul class="forum-selections"' not in html
    assert 'class="forum-selection"' in html
    for talk in review['talks']:
        assert talk['summary'] in unescape(html)
        assert len(talk['summary']) >= 50
    article = render.render_kvm_forum_review_html(Config(), kvm_forum.get_review(2026))
    assert 'VFIO selftests' not in article
    for paragraph in re.findall(r'<p>(.*?)</p>', article, flags=re.S):
        assert '**' not in paragraph, 'emphasis markers must not leak into visible prose'


@pytest.mark.parametrize('field,value,match', [
    ('source', '../private.md', 'local year'),
    ('talks', [], '5–10'),
])
def test_review_validation_rejects_incomplete_metadata(field, value, match):
    edition = deepcopy(kvm_forum.get_review(2026))
    edition['review'][field] = value
    with pytest.raises(ValueError, match=match):
        kvm_forum._validate_review(edition)


@pytest.mark.parametrize('count', [4, 11])
def test_annual_selection_policy_is_five_to_ten_not_a_fixed_count(count):
    edition = deepcopy(kvm_forum.get_review(2026))
    edition['review']['talks'] = (edition['review']['talks'] * 2)[:count]
    with pytest.raises(ValueError, match='5–10'):
        kvm_forum._validate_review(edition)


def test_validation_rejects_duplicate_talks_and_unverified_sources():
    original = kvm_forum.get_review(2026)
    edition = deepcopy(original)
    edition['review']['talks'][1]['id'] = edition['review']['talks'][0]['id']
    with pytest.raises(ValueError, match='unique'):
        kvm_forum._validate_review(edition)
    edition = deepcopy(original)
    edition['review']['talks'][0]['slides_format'] = 'abstract'
    with pytest.raises(ValueError, match='original slides'):
        kvm_forum._validate_review(edition)
    edition = deepcopy(original)
    edition['review']['talks'][0]['original_title'] = 'Outdated agenda title'
    with pytest.raises(ValueError, match='archived agenda'):
        kvm_forum._validate_review(edition)


def test_editorial_editions_override_archive_by_year_without_duplicate_entries(tmp_path, monkeypatch):
    titles = tmp_path / 'titles.json'
    titles.write_text(json.dumps({'editions': [{'year': 2026, 'titles': ['Older title']},
                                             {'year': 2025, 'titles': ['Historical title']}]}))
    monkeypatch.setattr(kvm_forum, 'TITLES_PATH', titles)
    editions, _ = kvm_forum.load_content()
    assert len({item['year'] for item in editions}) == len(editions)
    assert editions[-1]['year'] == 2026
    assert len(editions[-1]['titles']) == 43 and editions[-1]['review']


def test_review_preserves_benchmark_conditions_and_proposal_boundaries():
    html = render.render_kvm_forum_review_html(Config(), kvm_forum.get_review(2026))
    for term in ('scsi_debug', '100 µs', '驻留快照', '25 s', '约 20 s',
                 'PPTX', '客户机缓冲区准备', '1,000 个空闲沙箱',
                 '13.11 MB', 'virtio-scsi', '实验性 QMP', '毫秒级',
                 'QEMU RFC 尚待提交', '单线程', '36.075 s', '4.172 s',
                 '约 24 GB', '约 22 GB', '同宿主', 'RHEL 9.4',
                 '原始讲稿', '约 1.7 µs', '512 KiB', '尚缺少跨负载',
                 '增加线程后 IOPS 收益不足 3%', '未启用 TLS'):
        assert term in html, term


def test_historical_reviews_distinguish_predictions_and_benchmark_conditions():
    earliest = render.render_kvm_forum_review_html(Config(), kvm_forum.get_review(2010))
    for term in ('512 MB', '2 GB', '60 秒', '约为两倍', '定制负载', '5.67%', '40 个命令'):
        assert term in earliest
    colo = render.render_kvm_forum_review_html(Config(), kvm_forum.get_review(2013))
    assert '预测' in colo and 'COLO' in colo


def _request(path, method='GET'):
    handler = object.__new__(server.make_handler(Config()))
    handler.path, handler.headers, handler.wfile = path, {}, io.BytesIO()
    statuses, headers = [], {}
    handler.send_response = statuses.append
    handler.send_header = lambda name, value: headers.__setitem__(name, value)
    handler.end_headers = lambda: None
    getattr(handler, f'do_{method}')()
    return statuses[0], headers, handler.wfile.getvalue()


@pytest.mark.parametrize('method', ['GET', 'HEAD'])
def test_review_routes_and_assets_work_without_database_or_network(monkeypatch, method):
    def forbidden(*args, **kwargs):
        pytest.fail('Forum reading must not open a database or fetch sources')

    monkeypatch.setattr(server.db, 'connect', forbidden)
    monkeypatch.setattr(server.db, 'connect_readonly', forbidden)
    monkeypatch.setattr(kvm_forum, 'urlopen', forbidden)
    for path in ('/kvm-forum.html', '/kvm-forum/2010.html', '/kvm-forum/2026.html', '/assets/forum.css'):
        status, headers, body = _request(path, method)
        assert status == 200
        assert bool(body) is (method == 'GET')
        if path.endswith('.css'):
            assert headers['Content-Type'] == 'text/css; charset=utf-8'
            assert headers['Cache-Control'] == 'public, max-age=31536000, immutable'
    assert _request('/kvm-forum/2027.html', method)[0] == 404
    assert _request('/kvm-forum/9999.html', method)[0] == 404
    assert server._FORUM_REVIEW_ROUTE.fullmatch('/kvm-forum/../2026.html') is None


def test_static_export_includes_reviews_and_all_required_assets(tmp_path):
    editions, analysis = kvm_forum.load_content()
    before = deepcopy(editions)
    config = Config(render=Render(output_dir=tmp_path))
    render.render_kvm_forum(config, editions, analysis)
    assert editions == before
    assert not (tmp_path / 'kvm-forum/2027.html').exists()
    for edition in editions:
        if edition.get('review'):
            assert (tmp_path / edition['review']['path']).read_text() == render.render_kvm_forum_review_html(config, edition)
    assert (tmp_path / 'kvm-forum/2026.html').read_text() == render.render_kvm_forum_review_html(
        config, kvm_forum.get_review(2026))
    for name in render.FORUM_ASSET_NAMES:
        assert (tmp_path / 'assets' / name).read_bytes() == (render.ASSETS_DIR / name).read_bytes()
    package = tomllib.loads((Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text())
    packaged = package['tool']['setuptools']['package-data']['virt_report']
    assert 'content/kvm_forum/*.md' in packaged and 'content/kvm_forum/*.json' in packaged
