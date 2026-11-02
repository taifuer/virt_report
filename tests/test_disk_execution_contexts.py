"""Background domains stay visible without inventing extra I/O transitions."""
import json
from pathlib import Path

from virt_report.io_scenarios import build_io_scenarios


def test_disk_routes_explain_missing_execution_domains_without_fake_nodes():
    flows = Path(__file__).resolve().parents[1] / "virt_report/content/flows"
    base = json.loads((flows / "virtio-blk-read.json").read_text())
    routes = build_io_scenarios(base, flows)["routes"]
    for route in routes:
        contexts = {item["component"]: item for item in route["contexts"]}
        assert "host-kernel" in contexts
        for item in contexts.values():
            assert set(item) == {"component", "title", "context", "note"}
            assert all(isinstance(value, str) and value for value in item.values())
        if route["id"].startswith("spdk-"):
            assert "qemu" in contexts
            assert "不经过 QEMU 块层" in contexts["qemu"]["note"]
            assert "不走宿主文件系统" in contexts["host-kernel"]["note"]
        else:
            assert "hardware" in contexts
            if route["id"] == "rbd-blk":
                assert "远端 Ceph" in contexts["hardware"]["context"]
                assert "/dev/rbdX" in contexts["host-kernel"]["note"]
            else:
                assert "读取可命中宿主页缓存" in contexts["hardware"]["note"]
                assert "不等于落盘" in contexts["hardware"]["note"]
