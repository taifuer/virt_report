"""Shared report categories and evidence-backed overview fallbacks."""
from __future__ import annotations

VIRTIO_SOURCES = ("virtio-dev", "virtio-comment")
REPORT_PROJECTS = (
    ("qemu", "QEMU", ("qemu-devel", "qemu", "vhost-user")),
    ("kvm", "KVM", ("kvm",)),
    ("libvirt", "Libvirt", ("libvir-list", "libvirt")),
    ("virtio", "Virtio", VIRTIO_SOURCES),
)
OVERVIEW_PROJECTS = tuple(group for group in REPORT_PROJECTS if group[0] != "virtio")
_EMPTY_OVERVIEWS = {
    "本期未观察到显著动态", "本期无显著动态", "本期暂无显著动态",
    "本期未采集到新的社区活动", "本期未收录该项目的议题",
}


def source_in_report(source: str, period: str | None = None) -> bool:
    """Scope report selection and statistics; collection remains independent."""
    return period != "daily" or source not in VIRTIO_SOURCES


def projects_for_period(period: str) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Virtio protocol threads are selected only for weekly/monthly reports."""
    return OVERVIEW_PROJECTS if period == "daily" else REPORT_PROJECTS


def project_of(source: str) -> tuple[str, str]:
    """Map a configured source to its report category."""
    for key, name, members in REPORT_PROJECTS:
        if source in members:
            return key, name
    return "qemu", "QEMU"


def reconcile_overview(overview: list, sections: list,
                       candidates: list[dict] | None = None) -> list[dict]:
    """Repair core-project summaries and omit the repetitive Virtio overview.

    The fallback describes selection, not upstream activity or source coverage.
    Historical model text and billing remain untouched in the stored report.
    """
    rows = {row["project"].strip().casefold(): dict(row) for row in overview
            if isinstance(row, dict) and isinstance(row.get("project"), str)
            and row["project"].strip().casefold() != "virtio"}
    items_by_key = {}
    for section in sections:
        if not isinstance(section, dict):
            continue
        items = section.get("items", [])
        if isinstance(items, list):
            items_by_key.setdefault(section.get("key"), []).extend(
                item for item in items if isinstance(item, dict))
    # Protocol items carry their own concise commentary; do not repeat them in
    # the overview, including when rendering historical saved model output.
    for key, name, _members in OVERVIEW_PROJECTS:
        items = items_by_key.get(key, [])
        row = rows.get(name.casefold())
        if row is None and not items:
            continue
        row = row or {"project": name, "summary": ""}
        summary = str(row.get("summary") or "").strip()
        if (not summary or summary.rstrip("。.!！") in _EMPTY_OVERVIEWS
                or row.get("summary_source") == "items"):
            if items:
                titles = list(dict.fromkeys(
                    str(item.get("title") or item.get("original_title") or "").strip()
                    for item in items))
                titles = [title[:80] for title in titles if title][:2]
                summary = f"本期收录 {len(items)} 项"
                summary += ("：" + "；".join(titles)) if titles else "议题"
                summary += "等。" if len(items) > len(titles) and titles else "。"
                row["summary_source"] = "items"
            else:
                count = sum(project_of(card.get("project", ""))[0] == key
                            for card in candidates or [] if isinstance(card, dict))
                summary = (f"本期有 {count} 个候选议题，未选入正文。" if count else
                           "本期未收录该项目的议题。")
                row.pop("summary_source", None)
            row["summary"] = summary
        rows[name.casefold()] = row
    order = {name.casefold(): index for index, (_key, name, _members)
             in enumerate(REPORT_PROJECTS)}
    return sorted(rows.values(), key=lambda row: order.get(row["project"].casefold(), len(order)))
