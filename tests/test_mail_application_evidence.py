"""Mail application confirmations are evidence, not release announcements."""
import json

import pytest

from virt_report import db
from virt_report.processing.threads import rebuild_threads
from virt_report.summarize import evidence, prompts, report


def message(key, body='', *, parent=None, at='2026-10-15T01:00:00Z', **extra):
    return {'source': 'ml', 'project': 'qemu-devel', 'native_id': key,
            'message_id': key, 'in_reply_to': parent, 'thread_root': 'root' if parent else None,
            'created_at': at, 'updated_at': at, 'body_excerpt': body,
            'subject': 'Re: [PULL 0/2] queue' if parent else '[PULL 0/2] queue',
            'kind': 'discussion', 'author': 'Maintainer <maintainer@example.org>',
            'url': f'https://example.org/{key}', 'raw_json': {}, **extra}


@pytest.mark.parametrize('body', [
    'Applied, thanks.', 'Applied.', 'APPLIED, THANKS.',
    'Reviewed-by: Reviewer <reviewer@example.org>\n\nand pushed to git.',
    'On 15/10/26 14:20, Author wrote:\n\nApplied, thanks.\n\nr~',
])
def test_direct_reply_records_application_provenance(body):
    proof = evidence.mail_application_evidence(
        [message('root'), message('reply', body, parent='root')], 'root')
    assert proof['at'] == '2026-10-15T01:00:00Z'
    assert proof['author'] == 'Maintainer <maintainer@example.org>'
    assert proof['url'] == 'https://example.org/reply'
    assert proof['statement'] in body


@pytest.mark.parametrize('body', [
    '> Applied, thanks.', '   > Applied, thanks.',
    '<blockquote>\nApplied, thanks.\n</blockquote>',
    '---------- Forwarded message ---------\nApplied, thanks.',
    'Not applied.', 'Will apply, thanks.', 'If applied, thanks.',
    'Applied locally for testing.', 'Applied to my test tree.', 'Applied only patch 1.',
    'Reviewed-by: Reviewer <reviewer@example.org>', 'Acked-by: Reviewer',
    'diff --git a/x b/x\nApplied, thanks.',
    'Applied, thanks.\nI have reverted this change.',
    'Applied, thanks.\nBut reverted it afterwards.',
])
def test_quotes_plans_review_and_partial_or_reverted_application_do_not_count(body):
    assert not evidence.mail_application_evidence(
        [message('root'), message('reply', body, parent='root')], 'root')


def test_application_requires_actual_root_and_direct_reply():
    root = message('root', 'Applied, thanks.')
    child = message('patch-1', 'Patch body', parent='root')
    reply = message('reply', 'Applied, thanks.', parent='patch-1')
    assert not evidence.mail_application_evidence([root], 'root')
    assert not evidence.mail_application_evidence([root, child, reply], 'root')
    assert not evidence.mail_application_evidence([reply], 'missing-root')
    assert not evidence.mail_application_evidence(
        [root, message('reply', 'Applied, thanks.', parent='root',
                       subject='Re: [PULL 1/2] one patch')], 'root')
    assert evidence.mail_application_evidence(
        [message('reply', 'Applied, thanks.', parent='<root>'), root], 'root')


def test_later_revert_invalidates_application():
    assert not evidence.mail_application_evidence([
        message('root'), message('applied', 'Applied, thanks.', parent='root'),
        message('reverted', 'Reverted this change.', parent='root'),
    ], 'root')


def test_hyperkitty_archive_root_hash_maps_to_real_message_id(tmp_path):
    with db.connect(tmp_path / 'hyperkitty.db') as conn:
        root = message('root-message-id', project='libvir-list', thread_root='ROOT_HASH',
                       raw_json={'official_api': True, 'message_hash': 'ROOT_HASH',
                                 'thread_hash': 'ROOT_HASH'})
        reply = message('reply-message-id', 'Reviewed-by: Reviewer\n\nand pushed to git.',
                        project='libvir-list', parent='root-message-id', thread_root='ROOT_HASH',
                        raw_json={'official_api': True, 'message_hash': 'REPLY_HASH',
                                  'thread_hash': 'ROOT_HASH'})
        for item in (root, reply):
            db.upsert_item(conn, item)
        rebuild_threads(conn)
        end = '2026-10-15T16:00:00Z'
        rows = db.get_top_threads(conn, '2026-10-14T16:00:00Z', end)
        cards = report._build_threads_data(conn, rows, 600, None, end)
        assert cards[0]['state'] == 'applied'
        assert cards[0]['application_evidence']['statement'] == 'and pushed to git.'
        assert not evidence.mail_application_evidence([reply], 'ROOT_HASH')


def card_with_proof():
    proof = evidence.mail_application_evidence(
        [message('root'), message('reply', 'Applied, thanks.', parent='root')], 'root')
    return {'ref': 'T001', 'source': 'ml', 'project': 'qemu-devel', 'state': 'applied',
            'application_evidence': proof, 'subject': '[PULL 0/2] queue',
            'url': 'https://example.org/root', 'time': '2026-10-15'}


def test_application_allows_completion_but_never_release_or_immediate_availability():
    source = card_with_proof()
    for status in ('已应用', '已合入', '已合并'):
        assert evidence.consistency_error({'status': status}, source) is None
        assert evidence.consistency_error({'status': status}, {}) == 'unverified_completion'
    for status in ('已修复', '已发布', '可用'):
        assert evidence.consistency_error({'status': status}, source) == 'unverified_completion'
    assert evidence.consistency_error({'summary': '已正式发布。'}, source) == 'unverified_completion'
    assert evidence.consistency_error({'impact': '用户可直接使用。'}, source) == 'unconditional_availability'
    assert evidence.consistency_error({'status': '已应用'}, {'state': 'applied'}) == 'unverified_completion'
    _, sections = report._sanitize([], [{'items': [{'ref': 'T001', 'status': '已合并'}]}], [source])
    item = next(section for section in sections if section['items'])['items'][0]
    assert item['status'] == '已应用'
    assert item['url'] == source['url']
    assert item['application_evidence']['url'] == 'https://example.org/reply'


def test_as_of_application_survives_later_reply_but_cannot_leak_into_earlier_report(tmp_path):
    with db.connect(tmp_path / 'report.db') as conn:
        for item in [message('root'), message('applied', 'Applied, thanks.', parent='root',
                                            at='2026-10-15T12:00:00Z'),
                     message('later', 'A follow-up comment.', parent='root',
                             at='2026-10-15T13:00:00Z')]:
            db.upsert_item(conn, item)
        rebuild_threads(conn)
        def cards(end):
            rows = db.get_top_threads(conn, '2026-10-14T16:00:00Z', end)
            return report._build_threads_data(conn, rows, 600, None, end)
        before = cards('2026-10-15T12:00:00Z')
        assert before[0]['state'] == '' and before[0]['application_evidence'] == {}
        after = cards('2026-10-15T16:00:00Z')
        assert after[0]['state'] == 'applied'
        assert 'follow-up' in after[0]['latest_excerpt']
        assert after[0]['application_evidence']['at'] == '2026-10-15T12:00:00Z'
        prompt = prompts.build_prompt('daily', '2026-10-15', after)
        assert 'state=applied' in prompt and 'APPLICATION:' in prompt and 'Applied, thanks.' in prompt
        db.upsert_item(conn, message('future', 'Reverted this change.', parent='root',
                                     at='2026-10-16T00:00:00Z'))
        rebuild_threads(conn)
        assert cards('2026-10-15T16:00:00Z') == after
        assert 'future' not in json.dumps(after)
