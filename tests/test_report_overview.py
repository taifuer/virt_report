"""Overview selection statements must agree with the published sections."""
from copy import deepcopy

import pytest

from virt_report.config import Config
from virt_report.report_projects import reconcile_overview
from virt_report.render.render import render_report_html
from virt_report.summarize import prompts
from virt_report.summarize.report import _sanitize


def example_sections(key="qemu", name="QEMU"):
    return [{"key": key, "name": name, "items": [
        {"ref": "T001", "title": "virtio-net 设备 label 提案", "summary": "提案仍在评审。"},
        {"ref": "T002", "title": "virtio-msg 实现反馈", "summary": "传输层收到实现反馈。"},
    ]}]


@pytest.mark.parametrize("overview", [[], [{"project": "QEMU", "summary": ""}],
                                     [{"project": "QEMU", "summary": "本期未观察到显著动态。"}]])
def test_missing_or_empty_overview_uses_actual_items(overview):
    sections = example_sections()
    before = deepcopy((overview, sections))
    result = reconcile_overview(overview, sections)
    assert result == [{"project": "QEMU", "summary":
                       "本期收录 2 项：virtio-net 设备 label 提案；virtio-msg 实现反馈。",
                       "summary_source": "items"}]
    assert (overview, sections) == before
    assert reconcile_overview(result, sections) == result


def test_meaningful_overview_is_preserved_and_selection_counts_are_updated():
    overview = [{"project": "QEMU", "summary": "设备用途标识与传输层仍在讨论。"}]
    assert reconcile_overview(overview, example_sections()) == overview
    fallback = reconcile_overview([], example_sections())
    one = example_sections()
    one[0]["items"] = one[0]["items"][:1]
    assert "本期收录 1 项" in reconcile_overview(fallback, one)[0]["summary"]


def test_no_selected_items_does_not_claim_no_upstream_activity():
    overview = [{"project": "QEMU", "summary": "本期未观察到显著动态。"}]
    assert reconcile_overview(overview, [])[0]["summary"] == "本期未收录该项目的议题。"
    candidates = [{"project": "qemu-devel"}]
    assert "1 个候选议题，未选入正文" in reconcile_overview(overview, [], candidates)[0]["summary"]
    assert reconcile_overview([], [], []) == []


def test_regrouping_model_items_preserves_virtio_body_without_an_overview():
    cards = [{"ref": "T001", "project": "virtio-comment", "subject": "Virtio proposal",
              "time": "2026-11-05", "url": "https://example.test/proposal"}]
    overview, sections = _sanitize([], [{"key": "qemu", "items": [
        {"ref": "T001", "title": "规范提案", "summary": "正在讨论。"}]}], cards)
    assert not any(row["project"] == "Virtio" for row in overview)
    assert next(row for row in sections if row["key"] == "virtio")["items"]
    assert _sanitize(overview, sections, cards)[0] == overview


@pytest.mark.parametrize("period", ["daily", "weekly", "monthly"])
def test_prompt_schema_tracks_the_input_projects(period):
    cards = [{"project": "virtio-comment"}, {"project": "vhost-user"}, {"project": "virtio-dev"}]
    prompt = prompts.system_prompt(period, "example", cards)
    assert ("本次输入的项目为：QEMU。" if period == "daily"
            else "本次输入的项目为：QEMU、Virtio。") in prompt
    assert '"project": "Virtio"' not in prompt
    assert ('"key": "virtio"' in prompt) == (period != "daily")
    assert '"project": "KVM"' not in prompt
    assert "overview 固定包含" not in prompt
    assert "有条目时不得写" in prompt


@pytest.mark.parametrize("period,key", [("daily", "2026-11-08"), ("weekly", "2026-W45"),
                                      ("monthly", "2026-11")])
def test_historical_body_is_preserved_without_repeated_overview_or_mutation(period, key):
    content = {"period": period, "period_key": key, "label": key,
               "headline": "本周动态", "model": "deepseek-flash", "fallback": False,
               "stats": {}, "watchlist": [], "top_threads": [],
               "sections": example_sections("virtio", "Virtio"),
               "overview": [{"project": "Virtio", "summary": "本期未观察到显著动态。"}]}
    original = deepcopy(content)
    page = render_report_html(Config(), content)
    assert 'id="virtio"' in page and "virtio-net 设备 label 提案" in page
    assert 'id="overview"' not in page and 'href="#overview"' not in page
    assert "本期未观察到显著动态" not in page
    assert content == original


@pytest.mark.parametrize("summary", ["", "本期未观察到显著动态。", "有明确机制进展。"])
def test_virtio_overview_is_always_omitted_even_when_substantive(summary):
    overview = [{"project": " Virtio ", "summary": summary}]
    assert reconcile_overview(overview, example_sections("virtio", "Virtio")) == []
    assert reconcile_overview(overview, [], [{"project": "virtio-dev"}]) == []
