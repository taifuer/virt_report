"""从已生成报告中聚合运维与性能专题。"""
from __future__ import annotations

import json
from collections.abc import Iterable


TOPIC_RULES = (
    ("migration", "热迁移", "迁移链路、停机窗口、脏页收敛与跨主机兼容性", (
        "热迁移", "migration", "migrate", "multifd", "postcopy", "precopy",
        "switchover", "dirty page", "live-migration",
    )),
    ("live-upgrade", "热升级", "运行中软件更新、在线维护与服务连续性", (
        "热升级", "热更新", "live update", "live-update", "runtime update",
        "livepatch", "online update", "live upgrade", "live-upgrade",
    )),
    ("hotplug", "热插拔", "运行中增减 CPU、内存与设备的能力", (
        "热插拔", "hotplug", "hot-plug", "hot unplug", "hot-unplug",
        "vcpu unplug", "vcpu hotplug", "memory unplug", "memory hotplug",
        "device unplug", "device hotplug",
    )),
    ("lifecycle", "启动与生命周期", "启动、关机、重启、暂停恢复与生命周期可靠性", (
        "启动", "关机", "重启", "startup", "boot", "reboot", "shutdown", "reset",
        "suspend", "resume", "lifecycle", "firmware",
    )),
    ("performance", "虚机性能", "时延、吞吐、资源开销与硬件加速优化", (
        "性能", "performance", "optimize", "optimization", "latency", "throughput",
        "acceleration", "accelerate", "scalability", "benchmark", "overhead",
        "fast path", "fast-path", "zero-copy", "ioeventfd", "pml", "tph",
    )),
)


def classify_item(item: dict) -> list[str]:
    """返回一个报告条目命中的专题键；允许同时属于多个专题。"""
    text = " ".join(str(item.get(field, "")) for field in (
        "title", "original_title", "summary", "impact", "tag",
    )).lower()
    return [key for key, _name, _description, words in TOPIC_RULES
            if any(word in text for word in words)]


def build_topic_groups(report_rows: Iterable, limit: int = 60) -> list[dict]:
    """从 reports 查询结果构建去重后的专题分组。"""
    groups = {key: [] for key, *_rest in TOPIC_RULES}
    seen = {key: set() for key, *_rest in TOPIC_RULES}
    for row in report_rows:
        content = json.loads(row["content_json"])
        for section in content.get("sections", []):
            for raw in section.get("items", []):
                item = dict(raw)
                item["project"] = section.get("name", section.get("key", ""))
                item["report_period"] = row["period"]
                item["report_key"] = row["period_key"]
                identity = item.get("url") or item.get("ref") or item.get("title")
                for key in classify_item(item):
                    if identity in seen[key] or len(groups[key]) >= limit:
                        continue
                    seen[key].add(identity)
                    groups[key].append(item)
    return [{"key": key, "name": name, "description": description,
             "items": groups[key]}
            for key, name, description, _words in TOPIC_RULES]
