"""Scenario selectors expose only supported, independently reviewed presets."""
import copy
import json

import pytest

from virt_report.config import Config
from virt_report.execution_demos import CONTENT_DIR, DEMO_IDS, DEMO_PAGES, get_demo, load_demo, validate_selector
from virt_report.render.render import render_mechanisms_html


SELECTORS = json.loads((CONTENT_DIR / "execution-selectors.json").read_text(encoding="utf-8"))
for demo_id in DEMO_IDS:
    original = load_demo(demo_id)
    if "selector" in original:
        SELECTORS[demo_id] = original["selector"]


def groups(selector):
    if "categories" in selector:
        return [group for category in selector["categories"] for group in category["groups"]]
    return selector["groups"]


def test_selector_catalogue_matches_published_demos():
    assert set(SELECTORS) == set(DEMO_IDS)


@pytest.mark.parametrize("demo_id", DEMO_IDS)
def test_selector_represents_each_original_scenario_exactly_once(demo_id):
    original = load_demo(demo_id)
    expected = {scene["id"] for scene in original["scenarios"]}
    selector = SELECTORS[demo_id]
    validate_selector(selector, expected)
    options = [option["id"] for group in groups(selector) for option in group["scenarios"]]
    assert sorted(options) == sorted(expected)
    assert len(options) == len(set(options))
    assert get_demo(demo_id, presentation=False)["selector"] == selector
    if "selector" in original:
        assert original["selector"] == selector


def test_network_category_and_direction_preserve_supported_fallbacks():
    selector = SELECTORS["network-io"]
    assert [item["id"] for item in selector["operations"]] == ["tx", "rx"]
    assert [item["id"] for item in selector["categories"]] == ["qemu", "vhost", "dpdk", "vdpa"]
    for category in selector["categories"]:
        assert {group["operation"] for group in category["groups"]} == {"tx", "rx"}
        for group in category["groups"]:
            normal = f"{category['id']}-{group['operation']}"
            assert group["scenarios"][0]["id"] == normal
            expected_count = 2 if category["id"] == "qemu" else 1
            assert len(group["scenarios"]) == expected_count


def test_memory_object_selector_keeps_ram_conditions_together():
    selector = SELECTORS["memory-access"]
    assert "operations" not in selector
    ram, mmio = selector["categories"]
    assert [ram["id"], mmio["id"]] == ["ram", "mmio"]
    assert [group["label"] for group in ram["groups"] if not group.get("advanced")] == [
        "正常访问", "Guest 缺页", "KVM 建立映射",
    ]
    assert [option["id"] for group in ram["groups"] if not group.get("advanced")
            for option in group["scenarios"]] == [
        "ram-hit", "tlb-miss", "guest-page-fault", "ept-missing",
    ]
    assert [option["id"] for group in ram["groups"] if group.get("advanced")
            for option in group["scenarios"]] == ["host-page-fault", "dirty-write-protect"]
    assert [option["id"] for group in mmio["groups"] for option in group["scenarios"]] == [
        "mmio-read", "mmio-kernel-lapic", "mmio-ioeventfd", "mmio-vfio-direct", "mmio-ivshmem-direct",
    ]


def test_memory_advanced_presets_are_scoped_to_ram_and_keep_old_scene_ids():
    page = render_mechanisms_html(Config(), "memory-access")
    assert 'data-execution-advanced-category="ram" hidden' in page
    assert page.count('data-execution-advanced-scenario="') == 2
    for scene_id in ("host-page-fault", "dirty-write-protect"):
        assert f'data-execution-advanced-scenario="{scene_id}"' in page
        assert any(item["id"] == scene_id for item in get_demo("memory-access")["scenarios"])
    assert 'data-execution-advanced-category="mmio"' not in page


@pytest.mark.parametrize("demo_id,labels", [
    ("cpu-execution", ["Guest 直接执行", "KVM 内核处理", "QEMU 设备处理", "等待与唤醒"]),
    ("interrupt-delivery", ["软件后端", "直通设备", "屏蔽与等待"]),
])
def test_single_selector_groups_examples_without_free_combination(demo_id, labels):
    selector = SELECTORS[demo_id]
    assert "categories" not in selector and "operations" not in selector
    assert [group["label"] for group in selector["groups"]] == labels


def test_interrupt_main_menu_separates_sender_from_advanced_conditions():
    selector = SELECTORS["interrupt-delivery"]
    primary = [group for group in groups(selector) if not group.get("advanced")]
    advanced = [group for group in groups(selector) if group.get("advanced")]
    assert [[item["id"] for item in group["scenarios"]] for group in primary] == [
        ["msix-software", "irqfd", "apicv-posted"], ["vtd-remapped", "vtd-posted"],
    ]
    assert [item["id"] for item in advanced[0]["scenarios"]] == [
        "notify-suppressed", "msix-masked", "if-masked",
    ]
    page = render_mechanisms_html(Config(), "interrupt-delivery")
    assert '<details class="walkthrough-advanced" data-execution-advanced>' in page
    assert page.count('data-execution-advanced-scenario="') == 3


@pytest.mark.parametrize("demo_id", ["interrupt-delivery", "memory-access"])
@pytest.mark.parametrize("mutation", ["all-advanced", "non-boolean"])
def test_advanced_selector_requires_primary_choices_and_a_boolean_flag(demo_id, mutation):
    selector = copy.deepcopy(SELECTORS[demo_id])
    expected = {item["id"] for group in groups(selector) for item in group["scenarios"]}
    # Making just one category all-advanced must fail, even if another has primary choices.
    selected_groups = selector["categories"][0]["groups"] if "categories" in selector else selector["groups"]
    if mutation == "all-advanced":
        for group in selected_groups:
            group["advanced"] = True
    else:
        selected_groups[0]["advanced"] = "false"
    with pytest.raises(ValueError, match="Invalid execution selector"):
        validate_selector(selector, expected)


def test_advanced_selector_cannot_hide_a_direction_from_operation_controls():
    selector = copy.deepcopy(SELECTORS["network-io"])
    expected = {item["id"] for group in groups(selector) for item in group["scenarios"]}
    selector["categories"][0]["groups"][0]["advanced"] = True
    with pytest.raises(ValueError, match="cannot combine with operation controls"):
        validate_selector(selector, expected)


@pytest.mark.parametrize("mutation", [
    "missing-scene", "duplicate-scene", "unknown-scene", "missing-direction",
    "duplicate-direction", "unknown-direction", "missing-operation", "empty-label", "empty-category",
    "mixed-top-level", "duplicate-category",
])
def test_selector_rejects_incomplete_or_ambiguous_presets(mutation):
    selector = copy.deepcopy(SELECTORS["network-io"])
    category = selector["categories"][0]
    group = category["groups"][0]
    if mutation == "missing-scene":
        group["scenarios"].pop()
    elif mutation == "duplicate-scene":
        group["scenarios"].append(copy.deepcopy(group["scenarios"][0]))
    elif mutation == "unknown-scene":
        group["scenarios"][0]["id"] = "unknown"
    elif mutation == "missing-direction":
        category["groups"].pop()
    elif mutation == "duplicate-direction":
        category["groups"][1]["operation"] = "tx"
    elif mutation == "unknown-direction":
        category["groups"][1]["operation"] = "other"
    elif mutation == "missing-operation":
        del group["operation"]
    elif mutation == "empty-label":
        group["scenarios"][0]["label"] = " "
    elif mutation == "empty-category":
        category["groups"] = []
    elif mutation == "mixed-top-level":
        selector["groups"] = category["groups"]
    elif mutation == "duplicate-category":
        selector["categories"][1]["id"] = category["id"]
    original = json.loads((CONTENT_DIR / "network-io.json").read_text(encoding="utf-8"))
    expected = {scene["id"] for scene in original["scenarios"]}
    with pytest.raises(ValueError, match="Invalid execution selector"):
        validate_selector(selector, expected)


@pytest.mark.parametrize("demo_id", DEMO_IDS)
def test_selector_template_preserves_complete_no_script_fallback(demo_id):
    page = render_mechanisms_html(Config(), DEMO_PAGES[demo_id])
    selector = SELECTORS[demo_id]
    if demo_id == "disk-persistence":
        assert "data-vbr-completion" in page
        assert "data-execution-lab" not in page
        assert "FLUSH" in page and "FUA" in page
        assert "完成保证" in page and "稳定存储" in page
        return
    assert "data-execution-controls hidden" in page
    assert "data-execution-scenario-choice" in page
    assert ("data-execution-category" in page) == ("categories" in selector)
    assert ('data-execution-operation="tx"' in page) == (demo_id == "network-io")
    fallback = page.split('class="execution-fallback"', 1)[1]
    for scene in get_demo(demo_id)["scenarios"]:
        assert f'<summary>{scene["label"]}</summary>' in fallback
        assert scene["steps"][-1]["description"] in fallback
