"""Shared layout contracts: explanation, queue state and evidence stay distinct."""
import copy
import re

import pytest

from virt_report.config import Config
from virt_report.execution_demos import get_demo, validate_demo
from virt_report.render.render import render_mechanisms_html


@pytest.mark.parametrize("slug", [
    "virtio-blk-read", "network-io", "cpu-execution", "memory-access",
    "interrupt-delivery", "gpu-execution", "virtio-gpu-rendering", "libvirt-domain-start",
])
def test_inspector_contains_explanations_not_duplicate_source_lists(slug):
    page = render_mechanisms_html(Config(), slug)
    inspector = re.search(
        r'<aside\b[^>]*class="[^"]*walkthrough-inspector[^"]*"[^>]*>(.*?)</aside>',
        page, re.S,
    )
    assert inspector
    assert "<a " not in inspector.group(1)
    assert "source-links" not in inspector.group(1)
    assert "source-list" not in inspector.group(1)


@pytest.mark.parametrize("slug", [
    "virtio-blk-read", "network-io", "cpu-execution", "memory-access",
    "interrupt-delivery", "gpu-execution", "virtio-gpu-rendering", "libvirt-domain-start",
])
def test_step_explanation_precedes_full_width_visual_in_reading_order(slug):
    page = render_mechanisms_html(Config(), slug)
    workspace = page.index("walkthrough-workspace")
    explanation = page.index("walkthrough-inspector", workspace)
    visual_class = "vbr-visual" if slug == "virtio-blk-read" else "execution-canvas"
    visual = page.index(f'class="{visual_class}"', workspace)
    assert explanation < visual
    inspector_end = page.index("</aside>", explanation)
    inspector = page[explanation:inspector_end]
    assert 'class="walkthrough-step-heading"' in inspector
    assert 'class="walkthrough-step-body"' in inspector
    assert inspector.count('class="walkthrough-step-explanation"') == 1
    assert 'data-step-part="description"' in inspector
    assert 'data-step-part="detail"' in inspector
    assert "walkthrough-step-context" not in inspector
    assert "data-execution-connections" not in inspector
    assert "data-vbr-event" not in inspector
    # Both ordinary articles and full walkthroughs share the same reading order.
    assert inspector_end < visual


def test_disk_uses_one_queue_component_in_both_views():
    page = render_mechanisms_html(Config(), "virtio-blk-read")
    assert page.count("data-vbr-queue-overview") == 1
    assert page.count("data-vbr-queue-detail") == 1
    assert "data-vbr-descriptor-address" not in page
    assert "data-vbr-followup-queue" not in page
    assert "assets/walkthrough-components.js?v=" in page


def test_guest_mode_labels_follow_the_title_in_a_shared_heading_row():
    page = render_mechanisms_html(Config(), "virtio-blk-read")
    headings = re.findall(r'<strong class="vbr-node-heading">(.*?)</strong>', page, re.S)
    assert len(headings) == 2
    for heading, selector, label in zip(headings, ("app", "driver"), ("用户态", "内核态")):
        assert f'<b data-vbr-{selector}-title>' in heading
        assert heading.index(f'data-vbr-{selector}-title') < heading.index('class="vbr-mode-tag"')
        assert f'<span class="vbr-mode-tag">{label}</span>' in heading


@pytest.mark.parametrize("broken", ["stepIds", "sourceIds", "kind", "detail"])
def test_instruction_evidence_rejects_invalid_mapping(broken):
    demo = copy.deepcopy(get_demo("cpu-execution"))
    scene = demo["scenarios"][0]
    entry = {
        "id": "instruction", "title": "示例指令", "kind": "instruction",
        "context": "Guest 用户态", "detail": "不是宿主函数调用。",
        "stepIds": [scene["steps"][0]["id"]],
        "sourceIds": [next(iter(demo["sources"]))],
    }
    entry[broken] = ["unknown"] if broken.endswith("Ids") else ""
    scene["sourceSteps"] = [entry]
    with pytest.raises(ValueError):
        validate_demo(demo)
