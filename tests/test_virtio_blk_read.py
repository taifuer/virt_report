"""Offline source-reviewed read walkthrough and deployable export contracts."""
import copy
import json
import re
import shutil
import socket
import sqlite3
import subprocess
from pathlib import Path

import pytest

from virt_report import mechanisms, server
from virt_report.config import Config, Render
from virt_report.render import render


@pytest.fixture(autouse=True)
def clear_editorial_caches():
    mechanisms.get_flow.cache_clear()
    mechanisms.get_io_scenarios.cache_clear()
    render._render_mechanism_page.cache_clear()
    yield
    mechanisms.get_flow.cache_clear()
    mechanisms.get_io_scenarios.cache_clear()
    render._render_mechanism_page.cache_clear()


def test_flow_is_allowlisted_and_matches_article_source_baselines():
    article = mechanisms.get_article("virtio-blk-read")
    flow = mechanisms.get_flow(article["flow"])
    assert article["presentation"] == "walkthrough"
    assert article["topic"] == "block-io-walkthrough"
    assert article["title"] == "磁盘 I/O 读写过程"
    assert article["baselines"] == flow["baselines"]
    assert flow["request"]["bytes"] == 4096
    assert flow["request"]["queueSize"] == 8
    assert 8 <= len(flow["phases"]) <= 12
    assert 20 <= len(flow["steps"]) <= 36
    assert {phase["id"] for phase in flow["phases"]} == {step["phase"] for step in flow["steps"]}
    assert {step["actor"] for step in flow["steps"]} >= {
        "guest-user", "guest", "kvm", "qemu", "backend",
    }
    assert all(step["sourceIds"] for step in flow["steps"])
    for value in ("../mechanisms", "../../.env", "virtio-blk-read.json", "unknown"):
        assert mechanisms.get_flow(value) is None


@pytest.mark.parametrize("mutation", [
    "wrong-id", "moving-baseline", "external-source", "different-commit", "source-traversal",
    "missing-reference", "empty-references", "duplicate-step", "invalid-line",
    "malformed-baseline", "malformed-source", "malformed-step",
    "missing-paths", "invalid-path", "missing-path-source", "duplicate-path",
])
def test_flow_rejects_invalid_editorial_sources(tmp_path, monkeypatch, mutation):
    flow = copy.deepcopy(mechanisms.get_flow("virtio-blk-read"))
    first_source = next(iter(flow["sources"]))
    if mutation == "wrong-id":
        flow["id"] = "another-flow"
    elif mutation == "moving-baseline":
        flow["baselines"][0]["commit"] = "master"
    elif mutation == "external-source":
        flow["sources"][first_source]["url"] = "javascript:alert(1)"
    elif mutation == "different-commit":
        source = flow["sources"][first_source]
        source["url"] = re.sub(r"/blob/[0-9a-f]{40}/", "/blob/" + "0" * 40 + "/", source["url"])
    elif mutation == "source-traversal":
        flow["sources"][first_source]["path"] = "../../.env"
    elif mutation == "missing-reference":
        flow["steps"][0]["sourceIds"] = ["unknown-source"]
    elif mutation == "empty-references":
        flow["steps"][0]["sourceIds"] = []
    elif mutation == "duplicate-step":
        flow["steps"][1]["id"] = flow["steps"][0]["id"]
    elif mutation == "invalid-line":
        flow["sources"][first_source]["line"] = True
    elif mutation == "malformed-baseline":
        flow["baselines"] = ["not-an-object"]
    elif mutation == "malformed-source":
        flow["sources"][first_source] = "not-an-object"
    elif mutation == "malformed-step":
        flow["steps"][0] = "not-an-object"
    elif mutation == "missing-paths":
        flow.pop("callPaths", None)
    elif mutation == "invalid-path":
        flow["callPaths"] = ["not-an-object"]
    elif mutation == "missing-path-source":
        flow["callPaths"][0]["sourceIds"] = ["unknown-source"]
    elif mutation == "duplicate-path":
        flow["callPaths"][1]["id"] = flow["callPaths"][0]["id"]
    directory = tmp_path / "content" / "flows"
    directory.mkdir(parents=True)
    (directory / "virtio-blk-read.json").write_text(json.dumps(flow), encoding="utf-8")
    monkeypatch.setattr(mechanisms, "__file__", str(tmp_path / "mechanisms.py"))
    mechanisms.get_flow.cache_clear()
    with pytest.raises(ValueError):
        mechanisms.get_flow("virtio-blk-read")


def test_walkthrough_embeds_safe_data_and_loads_model_before_ui_without_network(monkeypatch):
    def unexpected_io(*args, **kwargs):
        pytest.fail("Rendering an editorial walkthrough must not use a network or database")

    monkeypatch.setattr(socket, "create_connection", unexpected_io)
    monkeypatch.setattr(sqlite3, "connect", unexpected_io)
    page = render.render_mechanisms_html(Config(), "virtio-blk-read")
    assert "磁盘 I/O 读写过程 - virt-report" in page
    assert "mechanism-article--walkthrough" in page
    assert "mechanism-layout--walkthrough" in page
    assert 'class="mechanism-walkthrough-nav"' in page
    assert 'class="mechanism-toc"' not in page
    match = re.search(r'<script\b(?=[^>]*\btype="application/json")'
                      r'(?=[^>]*\bid="virtio-blk-read-data")[^>]*>(.*?)</script>', page, re.S)
    assert match is not None
    data = json.loads(match.group(1))
    assert data == mechanisms.get_flow("virtio-blk-read")
    catalog = re.search(r'<script id="block-io-scenarios-data" type="application/json">'
                        r'(.*?)</script>', page, re.S)
    assert catalog is not None
    assert json.loads(catalog.group(1)) == mechanisms.get_io_scenarios()
    assert len(json.loads(catalog.group(1))["routes"]) == 5
    assert page.index("assets/mechanism-viewer.js?v=") < page.index("assets/virtio-blk-read-model.js?v=")
    assert page.index("assets/virtio-blk-read-model.js?v=") < page.index("assets/virtio-blk-read.js?v=")
    assert "/root/codes/" not in page
    assert 'id="preface"' in page and 'id="walkthrough"' in page and 'id="sources"' in page


def test_walkthrough_has_catalogue_and_existing_article_entry_points():
    catalogue = render.render_mechanisms_html(Config())
    assert "mechanism-walkthrough-entry" not in catalogue
    assert 'href="mechanisms/virtio-blk-read.html#walkthrough"' not in catalogue
    assert catalogue.count('href="mechanisms/virtio-blk-read.html"') == 1
    writing = render.render_mechanisms_html(Config(), "qemu-virtio-blk")
    assert 'href="virtio-blk-read.html"' in writing
    spdk = render.render_mechanisms_html(Config(), "spdk-vhost-user-blk")
    assert 'href="virtio-blk-read.html?backend=spdk-blk&amp;operation=write#walkthrough"' in spdk


def test_walkthrough_links_queue_basics_and_backend_sources():
    page = render.render_mechanisms_html(Config(), "virtio-blk-read")
    source_start = page.index('<section id="sources"')
    sources = page[source_start:page.index('</section>', source_start)]
    for slug in ("virtio-device-initialization", "packed-virtqueue", "spdk-vhost-user-blk"):
        assert f'href="{slug}.html"' in page
    assert "缓存与持久化" in page
    assert "普通文件与写入的差异" not in page
    ids = {"local-scsi": "scsi-qemu-vq", "rbd-blk": "rbd-submit",
           "spdk-blk": "spdk-blk-vq", "spdk-scsi": "spdk-scsi-vq"}
    for route in mechanisms.get_io_scenarios()["routes"]:
        if route["id"] in ids:
            source = route["scenarios"]["read"]["sources"][ids[route["id"]]]
            assert f'href="{source["url"]}"' in sources


def test_walkthrough_has_distinct_views_guest_domains_and_no_sharing_ui():
    page = render.render_mechanisms_html(Config(), "virtio-blk-read")
    for view, label in (("overview", "流程总览"), ("mechanism", "关键结构"), ("source", "源码路径")):
        assert re.search(rf'<button\b[^>]*data-vbr-view="{view}"[^>]*>{label}</button>', page)
    assert "data-vbr-callpaths" in page
    assert "vbr-guest-user" in page and "vbr-guest-kernel" in page
    assert 'data-vbr-node="application"' in page
    assert 'data-vbr-operation="read"' in page and 'data-vbr-operation="write"' in page
    assert 'data-vbr-progress aria-label="演示阶段"' in page
    assert 'aria-label="读取阶段"' not in page
    assert 'data-vbr-backend-select' in page
    assert all(f'data-vbr-irq-stage="{stage}"' in page for stage in (
        "signal", "pending", "injected", "handled",
    ))
    assert "data-vbr-share" not in page
    script = (render.ASSETS_DIR / "virtio-blk-read.js").read_text(encoding="utf-8")
    assert "navigator.clipboard" not in script
    assert "data-vbr-share" not in script


def test_step_explanations_are_grouped_in_the_inspector_not_below_the_diagram():
    page = render.render_mechanisms_html(Config(), "virtio-blk-read")
    visual_start = page.index('<div class="vbr-visual">')
    inspector_start = page.index('<aside class="vbr-inspector walkthrough-inspector"')
    assert inspector_start < visual_start
    visual = page[visual_start:page.index('<div class="vbr-fallback"', visual_start)]
    inspector = page[inspector_start:page.index("</aside>", inspector_start)]
    assert "data-vbr-ownership" not in visual
    assert "data-vbr-ownership" in inspector
    assert page.count("data-vbr-ownership") == 1
    assert re.search(r'<span\b(?=[^>]*\bdata-vbr-ownership\b)'
                     r'(?=[^>]*\bdata-step-part="state")[^>]*>', inspector)
    assert "data-vbr-event" not in inspector
    assert re.search(r'<p class="sr-only" data-vbr-event', visual)


def test_backend_selection_is_part_of_the_playback_not_a_separate_comparison():
    page = render.render_mechanisms_html(Config(), "virtio-blk-read")
    assert 'id="backends"' not in page
    assert "接口与后端对照" not in page
    start = page.index('data-vbr-lab')
    select = page.index('data-vbr-backend-select')
    toolbar = page.index('data-vbr-controls')
    assert start < select < toolbar
    assert all(f'<option value="{route}">' in page for route in (
        "local-blk", "local-scsi", "rbd-blk", "spdk-blk", "spdk-scsi",
    ))
    assert 'data-vbr-control-plane' in page
    assert 'data-vbr-queue-overview' in page
    assert 'data-vbr-queue-detail' in page
    assert 'data-vbr-descriptor-address' not in page
    script = (render.ASSETS_DIR / "virtio-blk-read.js").read_text(encoding="utf-8")
    assert 'buildQueueSnapshot(scene, frame)' in script
    assert 'WalkthroughComponents.queue' in script
    assert 'data-vbr-source-list' not in page


def test_scenario_intro_is_one_paragraph_with_initialization_in_configuration():
    page = render.render_mechanisms_html(Config(), "virtio-blk-read")
    start = page.index('data-vbr-lab')
    toolbar = page.index('data-vbr-controls', start)
    intro = page[start:toolbar]
    assert intro.count("data-vbr-scenario-summary") == 1
    assert "data-vbr-control-plane" not in intro
    config_start = page.index('<details class="vbr-configuration walkthrough-configuration"')
    configuration = page[config_start:page.index("</details>", config_start)]
    assert "data-vbr-control-plane" in configuration
    assert page.count("data-vbr-control-plane") == 1
    for route in mechanisms.get_io_scenarios()["routes"]:
        if route["id"].startswith("spdk-"):
            for scene in route["scenarios"].values():
                assert "QEMU 仅负责控制面" in scene["summary"]


def test_backend_catalog_is_safely_embedded_and_versioned(monkeypatch):
    first = render.render_mechanisms_html(Config(), "virtio-blk-read")
    catalog = copy.deepcopy(mechanisms.get_io_scenarios())
    catalog["routes"][1]["scenarios"]["read"]["summary"] = '</script><script>alert("catalog")</script>'
    monkeypatch.setattr(mechanisms, "get_io_scenarios", lambda: catalog)
    render._render_mechanism_page.cache_clear()
    second = render.render_mechanisms_html(Config(), "virtio-blk-read")
    assert '<script>alert("catalog")</script>' not in second
    match = re.search(r'<script id="block-io-scenarios-data" type="application/json">'
                      r'(.*?)</script>', second, re.S)
    assert json.loads(match.group(1)) == catalog
    pattern = r'assets/virtio-blk-read\.js\?v=([0-9a-f]+)'
    assert re.search(pattern, first).group(1) != re.search(pattern, second).group(1)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for flow model checks")
@pytest.mark.parametrize("operation", ["read", "write"])
def test_application_return_follows_device_block_and_bio_completion(operation):
    program = """
const fs = require('node:fs');
const model = require(process.argv[1]);
const base = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const scene = model.getScenario(base, process.argv[3]);
process.stdout.write(JSON.stringify(model.buildTrace(scene).map(frame => ({id:frame.id,state:frame.state}))));
"""
    frames = json.loads(subprocess.check_output([
        "node", "-e", program,
        str(render.ASSETS_DIR / "virtio-blk-read-model.js"),
        str(Path(mechanisms.__file__).parent / "content" / "flows" / "virtio-blk-read.json"),
        operation,
    ], text=True, timeout=30))
    states = [frame["state"] for frame in frames]

    def first_true(field):
        return next(index for index, state in enumerate(states) if state[field])

    assert first_true("syscallEntered") < first_true("pagesMapped") <= first_true("blockSubmitted")
    assert first_true("blockSubmitted") <= first_true("published")
    assert first_true("interruptSignaled") < first_true("irqRouted") < first_true("irqPending")
    assert first_true("irqPending") < first_true("irqInjected") < first_true("guestNoticed")
    if operation == "write":
        assert first_true("bufferReady") < first_true("syscallEntered")
        assert first_true("bufferReady") < first_true("dataReady")
        assert states[-1]["usedLength"] == 1
    else:
        assert first_true("bufferReady") == first_true("dataReady")
        assert states[-1]["usedLength"] == 4097
    # bio_endio runs inside blk_mq_end_request; this teaching interleaving
    # resumes the synchronous reader after completion-side cleanup finishes.
    assert first_true("reclaimed") < first_true("bioCompleted") <= first_true("completed")
    assert first_true("completed") < first_true("userReturned") == len(states) - 1
    for state in states:
        if state["userReturned"]:
            assert all(state[field] for field in (
                "dataReady", "statusWritten", "usedPublished", "reclaimed", "completed", "bioCompleted",
            ))


def test_embedded_flow_escapes_script_closing_tags(monkeypatch):
    flow = copy.deepcopy(mechanisms.get_flow("virtio-blk-read"))
    flow["summary"] = '</script><script>alert("injected")</script>'
    monkeypatch.setattr(mechanisms, "get_flow", lambda slug: flow)
    page = render.render_mechanisms_html(Config(), "virtio-blk-read")
    match = re.search(r'<script id="virtio-blk-read-data" type="application/json">'
                      r'(.*?)</script>', page, re.S)
    assert match is not None
    assert json.loads(match.group(1)) == flow
    assert '<script>alert("injected")</script>' not in page


@pytest.mark.parametrize("path,marker", [
    ("/mechanisms/virtio-blk-read.html", "磁盘 I/O 读写过程"),
    ("/assets/virtio-blk-read-model.js", "buildTrace"),
    ("/assets/virtio-blk-read.js", "VirtioBlkReadModel"),
    ("/assets/virtio-blk-read.css", "mechanism-article--walkthrough"),
])
def test_walkthrough_routes_work_without_database(monkeypatch, path, marker):
    def forbidden(*args, **kwargs):
        pytest.fail("Walkthrough routes must not open the database")

    monkeypatch.setattr(server.db, "connect", forbidden)
    handler_type = server.make_handler(Config())
    handler = object.__new__(handler_type)
    handler.path = path
    responses = []
    handler._send = lambda code, body, *_args, **_kwargs: responses.append((code, body))
    handler._dispatch(head_only=False)
    code, body = responses[0]
    assert code == 200
    assert marker in (body.decode() if isinstance(body, bytes) else body)


def test_scene_content_changes_its_asset_version(monkeypatch):
    first = render.render_mechanisms_html(Config(), "virtio-blk-read")
    flow = copy.deepcopy(mechanisms.get_flow("virtio-blk-read"))
    flow["summary"] += " 更新解释。"
    monkeypatch.setattr(mechanisms, "get_flow", lambda slug: flow)
    render._render_mechanism_page.cache_clear()
    second = render.render_mechanisms_html(Config(), "virtio-blk-read")
    pattern = r'assets/virtio-blk-read\.js\?v=([0-9a-f]+)'
    assert re.search(pattern, first).group(1) != re.search(pattern, second).group(1)


def test_walkthrough_static_export_preserves_reports_and_embeds_scene(tmp_path):
    config = Config(render=Render(output_dir=tmp_path))
    preserved = tmp_path / "daily" / "2026-10-30.html"
    preserved.parent.mkdir()
    preserved.write_text("existing report", encoding="utf-8")
    exported = render.render_mechanisms(config)
    target = tmp_path / "mechanisms" / "virtio-blk-read.html"
    assert target in exported
    assert 'id="virtio-blk-read-data"' in target.read_text(encoding="utf-8")
    for name in ("virtio-blk-read.css", "virtio-blk-read-model.js", "virtio-blk-read.js"):
        assert (tmp_path / "assets" / name).read_bytes() == (render.ASSETS_DIR / name).read_bytes()
    assert preserved.read_text(encoding="utf-8") == "existing report"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for flow model checks")
def test_read_flow_model_invariants():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("test_virtio_blk_read_model.cjs"))],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
