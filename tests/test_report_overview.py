"""Overview selection statements must agree with the published sections."""
from copy import deepcopy

import pytest

from virt_report.config import Config
from virt_report.report_projects import reconcile_overview
from virt_report.render.render import render_report_html
from virt_report.summarize import prompts
from virt_report.summarize.report import _sanitize


def virtio_sections():
    return [{"key": "virtio", "name": "Virtio", "items": [
        {"ref": "T001", "title": "virtio-net 设备 label 提案", "summary": "提案仍在评审。"},
        {"ref": "T002", "title": "virtio-msg 实现反馈", "summary": "传输层收到实现反馈。"},
    ]}]


@pytest.mark.parametrize("overview", [[], [{"project": "Virtio", "summary": ""}],
                                     [{"project": "Virtio", "summary": "本期未观察到显著动态。"}]])
def test_missing_or_empty_overview_uses_actual_items(overview):
    sections = virtio_sections()
    before = deepcopy((overview, sections))
    result = reconcile_overview(overview, sections)
    assert result == [{"project": "Virtio", "summary":
                       "本期收录 2 项：virtio-net 设备 label 提案；virtio-msg 实现反馈。",
                       "summary_source": "items"}]
    assert (overview, sections) == before
    assert reconcile_overview(result, sections) == result


def test_meaningful_overview_is_preserved_and_selection_counts_are_updated():
    overview = [{"project": "Virtio", "summary": "设备用途标识与传输层仍在讨论。"}]
    assert reconcile_overview(overview, virtio_sections()) == overview
    fallback = reconcile_overview([], virtio_sections())
    one = virtio_sections()
    one[0]["items"] = one[0]["items"][:1]
    assert "本期收录 1 项" in reconcile_overview(fallback, one)[0]["summary"]


def test_no_selected_items_does_not_claim_no_upstream_activity():
    overview = [{"project": "Virtio", "summary": "本期未观察到显著动态。"}]
    assert reconcile_overview(overview, [])[0]["summary"] == "本期未收录该项目的议题。"
    candidates = [{"project": "virtio-dev"}]
    assert "1 个候选议题，未选入正文" in reconcile_overview(overview, [], candidates)[0]["summary"]
    assert reconcile_overview([], [], []) == []


def test_regrouping_model_items_cannot_lose_virtio_overview():
    cards = [{"ref": "T001", "project": "virtio-comment", "subject": "Virtio proposal",
              "time": "2026-11-05", "url": "https://example.test/proposal"}]
    overview, sections = _sanitize([], [{"key": "qemu", "items": [
        {"ref": "T001", "title": "规范提案", "summary": "正在讨论。"}]}], cards)
    virtio = next(row for row in overview if row["project"] == "Virtio")
    assert virtio["summary"] == "本期收录 1 项：规范提案。"
    assert next(row for row in sections if row["key"] == "virtio")["items"]
    assert _sanitize(overview, sections, cards)[0] == overview


@pytest.mark.parametrize("period", ["daily", "weekly", "monthly"])
def test_prompt_schema_tracks_the_input_projects(period):
    cards = [{"project": "virtio-comment"}, {"project": "vhost-user"}, {"project": "virtio-dev"}]
    prompt = prompts.system_prompt(period, "example", cards)
    assert "本次输入的项目为：QEMU、Virtio" in prompt
    assert '"project": "Virtio"' in prompt and '"key": "virtio"' in prompt
    assert '"project": "KVM"' not in prompt
    assert "overview 固定包含" not in prompt
    assert "有条目时不得写" in prompt


def test_historical_html_is_repaired_without_mutating_stored_model_data():
    content = {"period": "weekly", "period_key": "2026-W45", "label": "2026 年第 45 周",
               "headline": "本周动态", "model": "deepseek-flash", "fallback": False,
               "stats": {}, "watchlist": [], "top_threads": [], "sections": virtio_sections(),
               "overview": [{"project": "Virtio", "summary": "本期未观察到显著动态。"}]}
    original = deepcopy(content)
    page = render_report_html(Config(), content)
    assert "本期收录 2 项" in page
    assert "本期未观察到显著动态" not in page
    assert content == original
