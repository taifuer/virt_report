"""面向订阅器的报告 RSS 2.0 输出。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.etree import ElementTree as ET

from virt_report.config import Config
from virt_report.summarize import periods

ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("atom", ATOM_NS)

# RSS 是近期更新流，不承担完整归档。不同周期按更新频率保留合理窗口。
FEED_LIMITS = {None: 50, "daily": 30, "weekly": 26, "monthly": 24}


def _base_url(config: Config) -> str:
    return (config.render.site_url or "http://127.0.0.1:8090").rstrip("/")


def _pub_date(value: str | None) -> str:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    return format_datetime(parsed.astimezone(timezone.utc))


def _rss(title: str, site_link: str, self_link: str, description: str,
         entries: list[dict]) -> str:
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    last_build = entries[0].get("published_at") if entries else None
    for tag, value in (("title", title), ("link", site_link),
                       ("description", description), ("language", "zh-CN"),
                       ("copyright", "© 2026 virt-report"),
                       ("generator", "virt-report"),
                       ("lastBuildDate", _pub_date(last_build))):
        ET.SubElement(channel, tag).text = value
    ET.SubElement(channel, f"{{{ATOM_NS}}}link", {
        "href": self_link, "rel": "self", "type": "application/rss+xml",
    })
    for entry in entries:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = entry["title"]
        ET.SubElement(item, "link").text = entry["link"]
        ET.SubElement(item, "guid", {"isPermaLink": "true"}).text = entry["link"]
        ET.SubElement(item, "pubDate").text = _pub_date(entry.get("published_at"))
        ET.SubElement(item, "category").text = entry["category"]
        ET.SubElement(item, "description").text = entry.get("description") or ""
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def feed_http_headers(body: str) -> dict[str, str]:
    """为动态 Feed 生成稳定的条件请求头。"""
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    headers = {"ETag": f'"{digest}"'}
    try:
        last_modified = ET.fromstring(body).findtext("./channel/lastBuildDate")
    except ET.ParseError:
        last_modified = None
    if last_modified:
        headers["Last-Modified"] = last_modified
    return headers


def is_not_modified(request_headers, response_headers: dict[str, str]) -> bool:
    """按 HTTP 条件请求优先级判断订阅器缓存是否仍然有效。"""
    if_none_match = request_headers.get("If-None-Match")
    if if_none_match:
        candidates = {value.strip() for value in if_none_match.split(",")}
        return "*" in candidates or response_headers["ETag"] in candidates
    return bool(
        response_headers.get("Last-Modified")
        and request_headers.get("If-Modified-Since")
        == response_headers["Last-Modified"]
    )


def report_feed(conn: sqlite3.Connection, config: Config, period: str | None = None) -> str:
    base = _base_url(config)
    params: tuple = ()
    where = ""
    if period:
        where, params = "WHERE period=?", (period,)
    rows = conn.execute(
        "SELECT period,period_key,generated_at,content_json FROM reports " + where +
        " ORDER BY generated_at DESC", params,
    ).fetchall()
    entries = []
    labels = {"daily": "日报", "weekly": "周报", "monthly": "月报"}
    for row in rows:
        content = json.loads(row["content_json"])
        if content.get("fallback"):
            continue
        overview = " ".join(item.get("summary", "") for item in content.get("overview", []))
        entries.append({
            "title": f"{periods.label(row['period'], row['period_key'])} {labels[row['period']]}",
            "link": f"{base}/{row['period']}/{row['period_key']}.html",
            "published_at": row["generated_at"],
            "description": content.get("headline") or overview,
            "category": labels[row["period"]],
        })
        if len(entries) >= FEED_LIMITS[period]:
            break
    suffix = labels.get(period, "报告")
    feed_path = f"/{period}/feed.xml" if period else "/feed.xml"
    return _rss(f"virt-report {suffix}", base + "/", base + feed_path,
                "Libvirt、QEMU、KVM 虚拟化社区中文动态", entries)
