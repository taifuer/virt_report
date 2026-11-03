"""Offline validation, rendering and model tests for execution walkthroughs."""
import copy
import html
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from virt_report import execution_demos
from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render.render import render_mechanisms_html


@pytest.mark.parametrize("demo_id", execution_demos.DEMO_IDS)
def test_execution_demo_integrity_and_rendering(demo_id):
    demo = execution_demos.get_demo(demo_id)
    execution_demos.validate_demo(demo)
    page = render_mechanisms_html(Config(), execution_demos.DEMO_PAGES[demo_id])
    if demo_id == "disk-persistence":
        assert 'data-vbr-completion' in page
        assert 'data-execution-lab' not in page
        assert 'assets/disk-completion-model.js?v=' in page
        assert 'F1' in page and 'FUA' in page
        return
    assert 'data-execution-lab' in page
    assert 'data-execution-controls hidden' in page
    assert 'data-execution-fallback' in page
    assert '../assets/execution-model.js?v=' in page
    assert '../assets/execution-demo.js?v=' in page
    assert 'id="execution-demo-data"' in page
    assert page.count('id="execution-demo-data"') == 1
    for asset in ('execution-demo.js', 'execution-model.js', 'walkthrough-fullscreen.js',
                  'walkthrough-routing.js', 'execution-demo.css', 'walkthrough.css'):
        assert page.count(f'assets/{asset}?v=') == 1
    for scene in demo["scenarios"]:
        assert scene["label"] in page
        assert len(scene["steps"]) > 2
        assert any(step["focus"] for step in scene["steps"][1:])
        for step in scene["steps"]:
            assert step["description"] in html.unescape(page)


def test_execution_demo_lookup_does_not_allow_arbitrary_files():
    for name in ("../.env", "memory-access.json", "unknown", "../../config"):
        assert execution_demos.get_demo(name) is None


def test_demo_registry_points_to_existing_articles_without_duplicate_directory_entries():
    articles = mechanisms.articles()
    for demo_id, page_id in execution_demos.DEMO_PAGES.items():
        article = next(article for article in articles if article['slug'] == page_id)
        assert article['execution_demo'] == demo_id
    assert sum(article.get('presentation') == 'walkthrough' for article in articles) == 7
    assert not {'disk-persistence', 'vm-lifecycle'} & {article['slug'] for article in articles}


def test_embedded_labs_share_assets_without_overwriting_primary_disk_controls():
    page = render_mechanisms_html(Config(), "virtio-blk-read")
    assert 'data-vbr-lab' in page and 'data-execution-lab' not in page
    assert 'data-vbr-completion' in page
    assert '../assets/virtio-blk-read.js?v=' in page
    assert 'href="#persistence"' in page
    lifecycle = render_mechanisms_html(Config(), "libvirt-domain-start")
    assert 'execution-lab--embedded' in lifecycle
    assert 'href="#lifecycle"' in lifecycle
    assert 'class="mechanism-toc"' in lifecycle


@pytest.mark.parametrize("mutation", ["unknown-node", "unknown-field", "unsafe-source",
                                    "moving-source", "duplicate-step", "active-initial",
                                    "missing-source", "duplicate-field", "empty-initial"])
def test_execution_demo_rejects_invalid_content(mutation):
    demo = copy.deepcopy(execution_demos.get_demo("memory-access"))
    scene = demo["scenarios"][0]
    if mutation == "unknown-node":
        scene["steps"][1]["active"] = ["nonexistent"]
    elif mutation == "unknown-field":
        scene["steps"][1]["changes"]["nonexistent"] = "1"
    elif mutation == "unsafe-source":
        next(iter(demo["sources"].values()))["url"] = "javascript:alert(1)"
    elif mutation == "moving-source":
        next(iter(demo["sources"].values()))["url"] = "https://github.com/qemu/qemu/blob/master/file.c"
    elif mutation == "duplicate-step":
        scene["steps"][1]["id"] = scene["steps"][0]["id"]
    elif mutation == "active-initial":
        scene["steps"][0]["active"] = [scene["nodes"][0]["id"]]
    elif mutation == "missing-source":
        scene["steps"][1]["sourceIds"] = ["missing"]
    elif mutation == "duplicate-field":
        scene["structures"][0]["fields"].append(scene["structures"][0]["fields"][0].copy())
    elif mutation == "empty-initial":
        scene["nodes"][0]["initial"] = " "
    with pytest.raises(ValueError):
        execution_demos.validate_demo(demo)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for pure model tests")
def test_execution_model_cumulative_snapshots_and_projection():
    result = subprocess.run(
        ["node", "--test", "tests/test_execution_model.cjs"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_execution_content_is_packaged_and_uses_no_live_services():
    project = Path(__file__).resolve().parents[1]
    package = (project / "pyproject.toml").read_text()
    assert "content/flows/*.json" in package
    ui = (project / "virt_report/render/assets/execution-demo.js").read_text()
    assert "fetch(" not in ui and "XMLHttpRequest" not in ui and "WebSocket" not in ui
    # All values are plain data; inserting server-rendered JSON cannot terminate its script.
    for demo_id in execution_demos.DEMO_IDS:
        json.dumps(execution_demos.get_demo(demo_id), ensure_ascii=False)
