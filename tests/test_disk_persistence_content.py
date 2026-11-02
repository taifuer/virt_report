"""Offline completion and durability contracts for the disk persistence lab."""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from virt_report.execution_demos import validate_demo


CONTENT = Path(__file__).parents[1] / "virt_report/content/flows/disk-persistence.json"
DEMO = json.loads(CONTENT.read_text(encoding="utf-8"))
SCENARIOS = {scene["id"]: scene for scene in DEMO["scenarios"]}


def snapshot(scene_id, step_id):
    scene = SCENARIOS[scene_id]
    state = {
        field["key"]: field["initial"]
        for structure in scene["structures"]
        for field in structure["fields"]
    }
    for step in scene["steps"]:
        state.update(step["changes"])
        if step["id"] == step_id:
            return state
    raise AssertionError(f"Unknown step {scene_id}/{step_id}")


def position(scene_id, step_id):
    return next(i for i, step in enumerate(SCENARIOS[scene_id]["steps"])
                if step["id"] == step_id)


def test_persistence_demo_has_three_distinct_completion_contracts():
    validate_demo(DEMO)
    assert DEMO["defaultScenario"] == "write-cache"
    assert set(SCENARIOS) == {"write-cache", "write-flush", "write-fua"}


@pytest.mark.parametrize("scene_id", SCENARIOS)
def test_persistence_starts_neutral_and_keeps_cache_layers_separate(scene_id):
    scene = SCENARIOS[scene_id]
    assert len(scene["nodes"]) <= 8
    assert len(scene["steps"]) <= 20
    assert not scene["steps"][0]["active"]
    assert not scene["steps"][0]["changes"]
    assert {"host-cache", "device-cache", "stable"} <= {node["id"] for node in scene["nodes"]}
    state = snapshot(scene_id, "initial")
    assert state["stableData"] == "A"
    assert state["persistence"] == "未确认"
    assert "O_DIRECT" in state["guestOpen"] or "REQ_FUA" in state["guestOpen"]
    assert state["qemuCache"] == "writeback=on · direct=off · no-flush=off"
    assert "availIdx" not in state and "usedIdx" not in state


def test_ordinary_write_can_return_before_any_persistence_confirmation():
    state = snapshot("write-cache", "write-return")
    assert state["writeReturn"] == "4096"
    assert state["completedCount"] == "1"
    assert state["stableData"] == "A"
    assert "未确认" in state["persistence"]
    assert "脏数据" in state["hostCache"]
    assert "未执行" in snapshot("write-cache", "write-boundary")["syncReturn"]


def test_flush_is_a_second_guest_command_after_write_and_before_fsync_return():
    scene_id = "write-flush"
    assert position(scene_id, "write-return") < position(scene_id, "sync-call")
    assert position(scene_id, "flush-publish") < position(scene_id, "stable-confirm")
    assert position(scene_id, "stable-confirm") < position(scene_id, "flush-complete")
    assert position(scene_id, "flush-complete") < position(scene_id, "sync-return")
    pending = snapshot(scene_id, "flush-publish")
    assert pending["publishedCount"] == "2"
    assert pending["completedCount"] == "1"
    assert "VIRTIO_BLK_T_FLUSH" in pending["command"]
    assert pending["stableData"] == "A"
    result = snapshot(scene_id, "sync-return")
    assert result["stableData"] == "B"
    assert result["completedCount"] == "2"
    assert result["syncReturn"].startswith("0：")


def test_fua_defers_the_only_guest_write_completion_until_stable():
    scene_id = "write-fua"
    assert "已形成的 REQ_FUA 写" in snapshot(scene_id, "initial")["guestOpen"]
    pending = snapshot(scene_id, "fua-backend-sync")
    assert pending["publishedCount"] == "1"
    assert pending["completedCount"] == "0"
    assert pending["stableData"] == "A"
    assert "SCSI WRITE" in pending["command"] and "FUA=1" in pending["command"]
    assert position(scene_id, "fua-stable") < position(scene_id, "fua-complete")
    result = snapshot(scene_id, "fua-return")
    assert result["stableData"] == "B"
    assert result["completedCount"] == result["publishedCount"] == "1"
    assert "不自动约束所有前序写入" in result["priorWrites"]
    assert "FUA 已满足" in result["writeReturn"]
    assert "pwrite 返回" not in " ".join(step["description"] for step in SCENARIOS[scene_id]["steps"])


def test_persistence_sources_use_fixed_versions_and_real_step_references():
    used = {source for scene in SCENARIOS.values() for step in scene["steps"]
            for source in step["sourceIds"]}
    assert used <= DEMO["sources"].keys()
    for source in DEMO["sources"].values():
        url = urlsplit(source["url"])
        assert url.scheme == "https" and url.hostname == "github.com"
        commit = url.path.split("/blob/")[1].split("/")[0]
        assert len(commit) == 40 and all(c in "0123456789abcdef" for c in commit)
        assert url.fragment.startswith("L")


def test_fragment_explains_direct_cache_no_flush_and_virtio_blk_fua_fallback():
    path = CONTENT.parents[2] / "render/templates/_disk_persistence_lab.html"
    article = path.read_text(encoding="utf-8")
    for text in ("同一个工作区", "cache.direct=on", "cache.no-flush=on",
                 "cache=none", "cache=unsafe", "POSTFLUSH", "原子性"):
        assert text in article
    assert "virtio-blk 的普通 OUT 命令没有这里的原生 FUA 位" in article
