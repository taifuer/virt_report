"""RSS 2.0 输出。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.etree import ElementTree as ET

from virt_report.config import Config
from virt_report.processing.topics import build_topic_detail
from virt_report.summarize import periods


def _base_url(config: Config) -> str:
    return (config.render.site_url or "http://127.0.0.1:8090").rstrip("/")


def _pub_date(value: str | None) -> str:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    return format_datetime(parsed.astimezone(timezone.utc))


def _rss(title: str, link: str, description: str, entries: list[dict]) -> str:
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    last_build = entries[0].get("published_at") if entries else None
    for tag, value in (("title", title), ("link", link), ("description", description),
                       ("language", "zh-CN"), ("lastBuildDate", _pub_date(last_build))):
        ET.SubElement(channel, tag).text = value
    for entry in entries:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = entry["title"]
        ET.SubElement(item, "link").text = entry["link"]
        ET.SubElement(item, "guid", {"isPermaLink": "true"}).text = entry["link"]
        ET.SubElement(item, "pubDate").text = _pub_date(entry.get("published_at"))
        ET.SubElement(item, "description").text = entry.get("description") or ""
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def report_feed(conn: sqlite3.Connection, config: Config, period: str | None = None) -> str:
    base = _base_url(config)
    params: tuple = ()
    where = ""
    if period:
        where, params = "WHERE period=?", (period,)
    rows = conn.execute(
        "SELECT period,period_key,generated_at,content_json FROM reports " + where +
        " ORDER BY generated_at DESC LIMIT 20", params,
    ).fetchall()
    entries = []
    labels = {"daily": "日报", "weekly": "周报", "monthly": "月报"}
    for row in rows:
        content = json.loads(row["content_json"])
        overview = " ".join(item.get("summary", "") for item in content.get("overview", []))
        entries.append({
            "title": f"{periods.label(row['period'], row['period_key'])} {labels[row['period']]}",
            "link": f"{base}/{row['period']}/{row['period_key']}.html",
            "published_at": row["generated_at"],
            "description": content.get("headline") or overview,
        })
    suffix = labels.get(period, "报告")
    feed_path = f"/{period}/feed.xml" if period else "/feed.xml"
    return _rss(f"virt-report {suffix}", base + feed_path,
                "Libvirt、QEMU、KVM 虚拟化社区中文动态", entries)


def security_feed(conn: sqlite3.Connection, config: Config) -> str:
    base = _base_url(config)
    group = build_topic_detail(conn, "security", page=1, per_page=20, sort="latest")
    entries = [{
        "title": item["title"], "link": item["url"],
        "published_at": item["activity_at"],
        "description": " · ".join(filter(None, [
            item.get("security_label"), " ".join(item.get("cve_ids", [])),
            item.get("summary"),
        ])),
    } for item in group["items"]]
    return _rss("virt-report 安全与漏洞", base + "/topics/security/feed.xml",
                "QEMU、KVM、Libvirt 安全缺陷、明确 CVE 与安全增强", entries)
