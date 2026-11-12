"""Interactive VirGL paths keep submission, execution and consumption separate."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from virt_report.config import Config
from virt_report.execution_demos import get_demo, validate_demo, validate_selector
from virt_report.mechanisms import get_article
from virt_report.render.render import render_mechanisms_html


SLUG = "virtio-gpu-rendering"
PAGE = "virtio-gpu"
SCENE_IDS = ("virgl-render", "virgl-decode", "virgl-encode")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo():
    return get_demo(SLUG)


def scene_for(demo, identifier):
    return next(scene for scene in demo["scenarios"] if scene["id"] == identifier)


def snapshot(scene, step_id):
    values = {field["key"]: field["initial"]
              for structure in scene["structures"] for field in structure["fields"]}
    for step in scene["steps"]:
        values.update(step["changes"])
        if step["id"] == step_id:
            return values
    raise AssertionError(f"Missing step: {step_id}")


def position(scene, step_id):
    return next(index for index, step in enumerate(scene["steps"])
                if step["id"] == step_id)


def test_gpu_rendering_is_an_interactive_demo_with_three_curated_paths(demo):
    validate_demo(demo)
    assert {scene["id"] for scene in demo["scenarios"]} == set(SCENE_IDS)
    assert demo["defaultScenario"] == "virgl-render"
    validate_selector(demo["selector"], set(SCENE_IDS))
    article = get_article(PAGE)
    assert article["execution_demo"] == SLUG
    assert article.get("presentation") != "walkthrough"


@pytest.mark.parametrize("scene_id", SCENE_IDS)
def test_gpu_initial_state_is_neutral_and_does_not_publish_a_request(demo, scene_id):
    scene = scene_for(demo, scene_id)
    initial = scene["steps"][0]
    assert initial["active"] == initial["links"] == []
    assert initial["changes"] == {}
    assert initial["nodeStates"] == {}
    values = snapshot(scene, initial["id"])
    assert values["controlAvail"] == "未发布"
    assert values["controlUsed"] == "未归还"
    assert values["fenceState"] == "未建立"
    assert values["appResult"] == "待完成"


@pytest.mark.parametrize("scene_id", SCENE_IDS)
def test_gpu_commands_complete_before_guest_fence_and_application_consumption(demo, scene_id):
    scene = scene_for(demo, scene_id)
    order = ["publish-submit", "dispatch-submit", "execute-gpu",
             "return-response", "signal-guest-fence", "consume-result"]
    positions = [position(scene, step_id) for step_id in order]
    assert positions == sorted(positions)
    published = snapshot(scene, "publish-submit")
    gpu_done = snapshot(scene, "execute-gpu")
    returned = snapshot(scene, "return-response")
    signalled = snapshot(scene, "signal-guest-fence")
    consumed = snapshot(scene, "consume-result")
    assert "SUBMIT_3D" in published["outerType"]
    assert published["fenceId"] != snapshot(scene, scene["steps"][0]["id"])["fenceId"]
    assert gpu_done["controlUsed"] == published["controlUsed"]
    assert gpu_done["fenceState"] == published["fenceState"]
    assert returned["controlUsed"] != gpu_done["controlUsed"]
    assert signalled["fenceState"] != returned["fenceState"]
    assert signalled["appResult"] == published["appResult"]
    assert consumed["appResult"] != signalled["appResult"]


def test_video_paths_keep_surface_copy_and_encoded_feedback_explicit(demo):
    decode = scene_for(demo, "virgl-decode")
    assert position(decode, "execute-gpu") < position(decode, "sync-surface")
    assert position(decode, "sync-surface") < position(decode, "copy-planes")
    assert position(decode, "copy-planes") < position(decode, "return-response")
    assert position(decode, "signal-guest-fence") < position(decode, "present-image")
    encode = scene_for(demo, "virgl-encode")
    assert position(encode, "execute-gpu") < position(encode, "sync-coded-output")
    assert position(encode, "sync-coded-output") < position(encode, "write-feedback")
    assert position(encode, "write-feedback") < position(encode, "return-response")
    initial = snapshot(encode, encode["steps"][0]["id"])
    feedback = snapshot(encode, "write-feedback")
    assert initial["feedbackStatus"] == "NOT_STARTED"
    assert initial["codedSize"] == "0"
    assert feedback["feedbackStatus"] == "SUCCESS"
    assert feedback["codedSize"] != initial["codedSize"]
    assert feedback["appResult"] == initial["appResult"]


def test_rendering_allows_cpu_work_before_fence_and_next_batch_before_presentation(demo):
    scene = scene_for(demo, "virgl-render")
    steps = {step["id"]: step for step in scene["steps"]}
    encoded = snapshot(scene, "encode-draw")
    assert "绘制调用已返回" in steps["encode-draw"]["nodeStates"]["application"]
    assert encoded["controlAvail"] == "未发布"
    assert encoded["fenceState"] == "未建立"
    assert "与 Q1 的宿主处理重叠" in steps["publish-submit"]["detail"]
    assert snapshot(scene, "publish-submit")["fenceState"] == "待完成"
    assert "下一批绘制可以与本帧执行重叠" in steps["consume-result"]["detail"]
    assert "渲染和显示各自的访问依赖" in steps["consume-result"]["detail"]
    assert "其他批次可独立推进" in steps["consume-result"]["nodeStates"]["application"]


def test_gpu_sources_are_pinned_for_qemu_linux_mesa_and_virglrenderer(demo):
    urls = [source["url"] for source in demo["sources"].values()]
    assert all(re.search(r"/(?:blob|tree)/[0-9a-f]{40}/", url) for url in urls)
    assert any("github.com/qemu/qemu/" in url for url in urls)
    assert any("github.com/torvalds/linux/" in url for url in urls)
    assert any("mesa/mesa/" in url for url in urls)
    assert any("virgl/virglrenderer/" in url for url in urls)
    for scene in demo["scenarios"]:
        assert all(step["sourceIds"] for step in scene["steps"])


@pytest.mark.parametrize("scene_id", SCENE_IDS)
def test_resource_and_submission_cards_advance_with_completion(demo, scene_id):
    scene = scene_for(demo, scene_id)
    nodes = {node["id"]: node.get("initial", "") for node in scene["nodes"]}
    for step in scene["steps"]:
        nodes.update(step["nodeStates"])
        if step["id"] == "publish-submit":
            assert "已提交" in nodes["mesa"]
            assert "等待 flush" not in nodes["mesa"]
        if step["id"] == "execute-gpu" and scene_id == "virgl-render":
            assert "颜色纹理已写入" in nodes["resources"]
            assert "尚未确认" in nodes["resources"]
        if step["id"] == "signal-guest-fence":
            assert "F1 已确认" in nodes["resources"]
    assert "等待 F1" not in nodes["resources"]
    assert "颜色目标为空" not in nodes["resources"]


def test_gpu_walkthrough_uses_shared_controls_and_an_offline_no_script_fallback(demo):
    page = render_mechanisms_html(Config(), PAGE)
    assert page.count('id="execution-demo-data"') == 1
    encoded = re.search(r'<script[^>]*id="execution-demo-data"[^>]*>(.*?)</script>',
                        page, flags=re.S)
    assert encoded is not None
    assert {scene["id"] for scene in json.loads(encoded.group(1))["scenarios"]} == set(SCENE_IDS)
    for control in ("scenario", "play", "prev", "next", "reset", "speed", "expand"):
        assert f"data-execution-{control}" in page
    for view in ("overview", "structure", "source"):
        assert f'data-execution-view="{view}"' in page
    for asset in ("execution-model.js", "execution-demo.js", "execution-demo.css",
                  "walkthrough-fullscreen.js", "walkthrough-routing.js", "walkthrough.css",
                  "virtio-gpu-rendering.js", "virtio-gpu-rendering.css"):
        assert page.count(f"assets/{asset}?v=") == 1
    fallback = page.split('class="execution-fallback"', 1)[1]
    for scene in demo["scenarios"]:
        assert f'<summary>{scene["label"]}</summary>' in fallback
        assert scene["steps"][-1]["description"] in fallback


def test_video_support_boundary_is_visible_before_the_player(demo):
    page = render_mechanisms_html(Config(), PAGE)
    introduction = page.split("data-execution-lab", 1)[0]
    assert "QEMU 10.2" in introduction
    assert "视频" in introduction
    assert re.search(r"(?:未|没有).{0,30}(?:启用|接入|提供)", introduction)
    for identifier in ("virgl-decode", "virgl-encode"):
        scene = scene_for(demo, identifier)
        assert "QEMU 10.2" in " ".join(scene["assumptions"])
        assert "视频" in scene["summary"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for model checks")
def test_gpu_playback_uses_immutable_cumulative_snapshots_and_reversible_steps(demo):
    script = r"""
const assert = require('node:assert/strict');
const model = require('./virt_report/render/assets/execution-model.js');
const demo = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
for (const scene of demo.scenarios) {
  const original = JSON.stringify(scene);
  const trace = model.buildTrace(scene);
  let state = Object.fromEntries(scene.structures.flatMap(group =>
    group.fields.map(field => [field.key, field.initial])));
  let nodes = Object.fromEntries(scene.nodes.map(node =>
    [node.id, node.initial || '准备就绪']));
  assert.deepEqual(trace[0].active, []);
  assert.deepEqual(trace[0].changed, []);
  trace.forEach((frame, index) => {
    state = {...state, ...scene.steps[index].changes};
    nodes = {...nodes, ...scene.steps[index].nodeStates};
    assert.deepEqual(frame.values, state);
    assert.deepEqual(frame.nodes, nodes);
    if (index) assert.notEqual(frame.values, trace[index - 1].values);
  });
  for (const view of ['overview', 'structure', 'source']) {
    const points = model.stops(trace, view);
    assert.equal(points[0], 0);
    points.forEach((point, index) => {
      assert.equal(model.next(trace, view, point, 1), points[Math.min(index + 1, points.length - 1)]);
      assert.equal(model.next(trace, view, point, -1), points[Math.max(index - 1, 0)]);
    });
    trace.forEach(frame => {
      const projected = model.project(trace, view, frame.index);
      assert(points.includes(projected) && projected <= frame.index);
    });
  }
  trace.at(-1).values.testOnly = 'not shared';
  assert(!Object.hasOwn(trace[0].values, 'testOnly'));
  assert.equal(JSON.stringify(scene), original);
}
"""
    result = subprocess.run(["node", "-e", script], input=json.dumps(demo),
                            text=True, capture_output=True, cwd=ROOT, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
