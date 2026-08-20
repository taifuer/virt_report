"""离线构建并查询社区议题搜索索引。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from typing import Any

from virt_report import db
from virt_report.processing import architecture, category


PROJECTS = (
    ("qemu", "QEMU"),
    ("kvm", "KVM"),
    ("libvirt", "Libvirt"),
)
CATEGORIES = (
    ("feature", "功能"),
    ("bug", "缺陷"),
    ("other", "其他"),
)
ARCHITECTURE_FILTERS = (
    ("x86", "x86"),
    ("Arm", "Arm"),
    ("other", "其他架构"),
)
OTHER_ARCHITECTURES = ("RISC-V", "s390x", "PowerPC", "LoongArch", "Hexagon")
PAGE_SIZES = (10, 20, 30)
SORTS = (
    ("relevance", "相关度优先"),
    ("latest", "最新优先"),
    ("oldest", "最早优先"),
)

_PROJECT_FIELDS = {
    "qemu-devel": ("qemu", "QEMU"),
    "qemu": ("qemu", "QEMU"),
    "kvm": ("kvm", "KVM"),
    "libvir-list": ("libvirt", "Libvirt"),
    "libvirt": ("libvirt", "Libvirt"),
}
_KIND_LABELS = {
    "patch": "Patch",
    "rfc": "RFC",
    "issue": "Issue",
    "mr": "MR",
    "bug": "缺陷",
    "discussion": "讨论",
}
_PERIOD_LABELS = {"daily": "日报", "weekly": "周报", "monthly": "月报"}

# 查询扩展只覆盖站点长期关注且语义明确的术语，不在请求时调用翻译或 LLM。
_QUERY_ALIASES = {
    "热迁移": ("live migration", "migration"),
    "迁移": ("migration",),
    "热升级": ("live update", "live upgrade"),
    "热插拔": ("hotplug", "hot plug"),
    "启动": ("boot", "startup"),
    "性能": ("performance", "optimization"),
    "内存": ("memory",),
    "虚拟机": ("virtual machine", "guest", "vm"),
    "虚机": ("virtual machine", "guest", "vm"),
    "嵌套虚拟化": ("nested virtualization", "nested kvm"),
    "安全": ("security", "cve"),
    "漏洞": ("vulnerability", "cve"),
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _project_fields(value: Any) -> tuple[str, str]:
    raw = _clean(value)
    key = raw.casefold()
    if key in _PROJECT_FIELDS:
        return _PROJECT_FIELDS[key]
    if "libvirt" in key:
        return "libvirt", "Libvirt"
    if "kvm" in key:
        return "kvm", "KVM"
    if "qemu" in key:
        return "qemu", "QEMU"
    return key or "other", raw or "其他"


def _source_fields(source: Any, project: Any = "") -> tuple[str, str]:
    raw = _clean(source)
    folded = raw.casefold()
    if folded == "gitlab" or "gitlab" in folded:
        return "gitlab", "GitLab"
    if folded in {"ml", "mailing_list"} or "邮件" in raw:
        return "mailing_list", "邮件列表"
    # 报告历史数据偶尔只有项目名；已知 GitLab 项目使用 collector 项目标识。
    if _clean(project).casefold() in {"qemu", "libvirt"} and not raw:
        return "gitlab", "GitLab"
    return folded or "community", raw or "社区"


def _stable_key(url: str, title: str, project: str) -> str:
    digest = hashlib.sha256(f"{url}\n{title}\n{project}".encode("utf-8")).hexdigest()
    return f"report:{digest[:24]}"


def _new_document(*, key: str, thread_key: str | None, title: str,
                  project: Any, source: Any, url: str = "",
                  first_seen: str = "", last_seen: str = "",
                  kind: str = "", salience_score: float = 0.0) -> dict:
    project_key, project_label = _project_fields(project)
    source_key, source_label = _source_fields(source, project)
    original_title = _clean(title)
    detected_category = category.classify_change(kind, original_title)
    detected_architectures = architecture.detect_architectures([original_title])
    return {
        "document_key": key,
        "thread_key": thread_key,
        "project": project_key,
        "project_label": project_label,
        "source": source_key,
        "source_label": source_label,
        "kind": _clean(kind),
        "category": detected_category,
        "architectures": detected_architectures,
        "topics": [],
        "original_title": original_title,
        "title_zh": "",
        "summary": "",
        "impact": "",
        "first_seen": _clean(first_seen),
        "last_seen": _clean(last_seen),
        "url": _clean(url),
        "curated": 0,
        "salience_score": float(salience_score or 0),
        "report_refs": [],
        "indexed_at": "",
    }


def _merge_list(current: list[str], incoming: Any) -> list[str]:
    values = architecture.normalize_architectures(
        incoming if isinstance(incoming, list) else _json_list(incoming)
    )
    return list(dict.fromkeys([*current, *values]))


def _parse_reports(conn: sqlite3.Connection, documents: dict[str, dict],
                   by_url: dict[str, dict]) -> None:
    rows = conn.execute(
        "SELECT period,period_key,generated_at,content_json FROM reports "
        "ORDER BY generated_at,period_key"
    ).fetchall()
    for row in rows:
        try:
            content = json.loads(row["content_json"])
        except (TypeError, ValueError):
            continue
        if content.get("fallback"):
            continue
        report_ref = {
            "period": row["period"],
            "period_label": _PERIOD_LABELS.get(row["period"], row["period"]),
            "period_key": row["period_key"],
            "href": f"{row['period']}/{row['period_key']}.html",
            "generated_at": row["generated_at"],
        }
        for section in content.get("sections", []):
            section_project = section.get("key") or section.get("name")
            for item in section.get("items", []):
                url = _clean(item.get("url"))
                original = _clean(item.get("original_title") or item.get("title"))
                if not original:
                    continue
                document = by_url.get(url) if url else None
                if document is None:
                    key = _stable_key(url, original, _clean(section_project))
                    document = documents.get(key)
                    if document is None:
                        document = _new_document(
                            key=key, thread_key=None, title=original,
                            project=section_project, source=item.get("source"),
                            url=url, first_seen=item.get("time"),
                            last_seen=item.get("time"), kind=item.get("kind"),
                        )
                        documents[key] = document
                        if url:
                            by_url[url] = document
                # 报告按生成时间升序处理，后生成的点评覆盖旧文本；收录记录全部保留。
                title = _clean(item.get("title"))
                if title and title != original:
                    document["title_zh"] = title
                if _clean(item.get("summary")):
                    document["summary"] = _clean(item.get("summary"))
                if _clean(item.get("impact")):
                    document["impact"] = _clean(item.get("impact"))
                if item.get("category") in dict(CATEGORIES):
                    document["category"] = item["category"]
                document["architectures"] = _merge_list(
                    document["architectures"], item.get("architectures", [])
                )
                if _clean(item.get("time")) > document["last_seen"]:
                    document["last_seen"] = _clean(item.get("time"))
                identity = (report_ref["period"], report_ref["period_key"])
                document["report_refs"] = [
                    ref for ref in document["report_refs"]
                    if (ref["period"], ref["period_key"]) != identity
                ]
                document["report_refs"].append(dict(report_ref))
                document["curated"] = 1


def _parse_topic_snapshots(conn: sqlite3.Connection, documents: dict[str, dict],
                           by_url: dict[str, dict]) -> None:
    rows = conn.execute(
        "SELECT topic_key,content_json FROM topic_snapshots"
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["content_json"])
        except (TypeError, ValueError):
            continue
        topic_name = _clean(payload.get("name") or row["topic_key"])
        for scope in ("curated", "recent"):
            for item in payload.get(scope, []):
                thread_key = _clean(item.get("thread_key"))
                url = _clean(item.get("url"))
                document = documents.get(thread_key) or by_url.get(url)
                if document is None:
                    original = _clean(item.get("title"))
                    if not original:
                        continue
                    key = thread_key or _stable_key(
                        url, original, _clean(item.get("project"))
                    )
                    document = _new_document(
                        key=key, thread_key=thread_key or None,
                        title=original, project=item.get("project"),
                        source=item.get("source"), url=url,
                        last_seen=item.get("activity_at"),
                    )
                    documents[key] = document
                    if url:
                        by_url[url] = document
                if topic_name and topic_name not in document["topics"]:
                    document["topics"].append(topic_name)
                if not document["summary"] and _clean(item.get("summary")):
                    document["summary"] = _clean(item.get("summary"))
                if not document["impact"] and _clean(item.get("impact")):
                    document["impact"] = _clean(item.get("impact"))
                if item.get("category") in dict(CATEGORIES):
                    document["category"] = item["category"]
                document["architectures"] = _merge_list(
                    document["architectures"], item.get("architectures", [])
                )
                # 专题公开快照中的条目已经经过报告证据或专题筛选，可作为默认结果。
                if scope == "curated" or item.get("report_keys"):
                    document["curated"] = 1


def _finalize(document: dict, indexed_at: str) -> dict:
    document["indexed_at"] = indexed_at
    document["report_refs"].sort(
        key=lambda item: (item.get("generated_at", ""), item.get("period", "")),
        reverse=True,
    )
    tags = [
        document["project"], document["project_label"], document["source_label"],
        document["kind"], category.category_label(document["category"]),
        *document["architectures"], *document["topics"],
    ]
    fields = (
        document["title_zh"], document["original_title"], document["summary"],
        document["impact"], *tags,
    )
    document["search_text"] = " ".join(
        part.casefold() for part in map(_clean, fields) if part
    )
    document["tags"] = " ".join(map(_clean, tags))
    return document


def refresh_index(conn: sqlite3.Connection) -> dict[str, Any]:
    """从线程、正式报告和专题快照原子重建离线搜索索引。"""
    documents: dict[str, dict] = {}
    by_url: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT thread_key,subject,source,project,kind,first_seen,last_seen,"
        "salience_score,topic_tag,url FROM threads"
    ):
        title = _clean(row["subject"])
        if not title:
            continue
        document = _new_document(
            key=row["thread_key"], thread_key=row["thread_key"], title=title,
            project=row["project"], source=row["source"], url=row["url"],
            first_seen=row["first_seen"], last_seen=row["last_seen"],
            kind=row["kind"], salience_score=row["salience_score"],
        )
        if row["topic_tag"]:
            document["topics"].append(_clean(row["topic_tag"]))
        documents[row["thread_key"]] = document
        if document["url"]:
            by_url[document["url"]] = document

    _parse_reports(conn, documents, by_url)
    _parse_topic_snapshots(conn, documents, by_url)
    indexed_at = db.now_utc_iso()
    # 公开搜索只收录已经进入正式报告或专题精选的内容。原始线程仅用于补齐
    # 英文标题、项目和架构元数据，不作为独立搜索结果。
    finalized = [
        _finalize(document, indexed_at) for document in documents.values()
        if document["original_title"] and document["curated"]
    ]
    rows = [(
        item["document_key"], item["thread_key"], item["project"],
        item["project_label"], item["source"], item["source_label"],
        item["kind"], item["category"],
        json.dumps(item["architectures"], ensure_ascii=False),
        json.dumps(item["topics"], ensure_ascii=False),
        item["original_title"], item["title_zh"], item["summary"], item["impact"],
        item["search_text"], item["first_seen"], item["last_seen"], item["url"],
        item["curated"], item["salience_score"],
        json.dumps(item["report_refs"], ensure_ascii=False), item["indexed_at"],
    ) for item in finalized]
    fts_rows = [(
        item["document_key"], item["title_zh"], item["original_title"],
        item["summary"], item["impact"], item["tags"],
    ) for item in finalized]
    curated_count = len(finalized)
    with db.transaction(conn):
        conn.execute("DELETE FROM search_documents_fts")
        conn.execute("DELETE FROM search_documents")
        conn.executemany(
            "INSERT INTO search_documents (document_key,thread_key,project,project_label,"
            "source,source_label,kind,category,architectures,topics,original_title,title_zh,"
            "summary,impact,search_text,first_seen,last_seen,url,curated,salience_score,"
            "report_refs,indexed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.executemany(
            "INSERT INTO search_documents_fts "
            "(document_key,title_zh,original_title,summary,impact,tags) "
            "VALUES (?,?,?,?,?,?)",
            fts_rows,
        )
        conn.execute(
            "INSERT OR REPLACE INTO search_index_state "
            "(singleton,refreshed_at,document_count,curated_count) VALUES (1,?,?,?)",
            (indexed_at, len(finalized), curated_count),
        )
    return {
        "refreshed_at": indexed_at,
        "document_count": len(finalized),
        "curated_count": curated_count,
    }


def index_status(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT refreshed_at,document_count,curated_count FROM search_index_state "
        "WHERE singleton=1"
    ).fetchone()
    return dict(row) if row else {
        "refreshed_at": None, "document_count": 0, "curated_count": 0,
    }


def _term_groups(query: str) -> tuple[list[list[str]], list[str]]:
    terms = list(dict.fromkeys(part.casefold() for part in query.split() if part))[:8]
    groups: list[list[str]] = []
    expansions: list[str] = []
    for term in terms:
        aliases = list(_QUERY_ALIASES.get(term, ()))
        groups.append(list(dict.fromkeys([term, *aliases])))
        expansions.extend(alias for alias in aliases if alias not in expansions)
    return groups, expansions


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_expression(groups: list[list[str]]) -> str:
    clauses = []
    for group in groups:
        eligible = [value for value in group if len(value.replace(" ", "")) >= 3]
        if not eligible:
            continue
        quoted = [f'"{value.replace(chr(34), chr(34) * 2)}"' for value in eligible]
        clauses.append("(" + " OR ".join(quoted) + ")")
    return " AND ".join(clauses)


def _highlight_parts(text: str, terms: list[str]) -> list[dict[str, Any]]:
    text = _clean(text)
    needles = sorted(
        {term for term in terms if term and term.casefold() in text.casefold()},
        key=len, reverse=True,
    )
    if not text or not needles:
        return [{"text": text, "match": False}] if text else []
    pattern = re.compile("(" + "|".join(map(re.escape, needles)) + ")", re.I)
    return [
        {"text": part, "match": bool(index % 2)}
        for index, part in enumerate(pattern.split(text)) if part
    ]


def search(conn: sqlite3.Connection, query: str, *, project: str = "",
           category_key: str = "", architecture_key: str = "",
           sort: str = "relevance", page: int = 1, per_page: int = 10) -> dict[str, Any]:
    """查询离线索引，返回可直接交给模板的分页结果。"""
    query = _clean(query)
    allowed_projects = {key for key, _label in PROJECTS}
    allowed_categories = {key for key, _label in CATEGORIES}
    allowed_architectures = {key for key, _label in ARCHITECTURE_FILTERS}
    allowed_sorts = {key for key, _label in SORTS}
    project = project if project in allowed_projects else ""
    category_key = category_key if category_key in allowed_categories else ""
    architecture_key = (
        architecture_key if architecture_key in allowed_architectures else ""
    )
    sort = sort if sort in allowed_sorts else "relevance"
    per_page = per_page if per_page in PAGE_SIZES else 10
    page = max(1, page)
    status = index_status(conn)
    base = {
        "query": query, "project": project,
        "category": category_key, "architecture": architecture_key,
        "sort": sort, "page": page,
        "per_page": per_page, "page_sizes": PAGE_SIZES,
        "projects": PROJECTS, "categories": CATEGORIES,
        "architectures": ARCHITECTURE_FILTERS, "sorts": SORTS,
        "items": [], "total": 0,
        "pages": 0, "expansions": [], "error": "", "index": status,
        "ready": bool(status["refreshed_at"]),
    }
    if not query:
        return base
    if len(query) < 2:
        base["error"] = "请至少输入 2 个字符。"
        return base
    if len(query) > 100:
        base["error"] = "搜索关键词请控制在 100 个字符以内。"
        return base
    if not base["ready"]:
        base["error"] = "搜索索引正在准备，请稍后再试。"
        return base

    groups, expansions = _term_groups(query)
    base["expansions"] = expansions
    fts_query = _fts_expression(groups)
    params: list[Any] = []
    if fts_query:
        source_sql = (
            "search_documents_fts f JOIN search_documents d "
            "ON d.document_key=f.document_key"
        )
        where = ["search_documents_fts MATCH ?"]
        params.append(fts_query)
        relevance = "bm25(search_documents_fts,0.0,8.0,10.0,5.0,3.0,1.0)"
    else:
        source_sql = "search_documents d"
        where = ["1=1"]
        relevance = "0.0"

    for group in groups:
        alternatives = []
        for value in group:
            alternatives.append("d.search_text LIKE ? ESCAPE '\\'")
            params.append(_like(value))
        where.append("(" + " OR ".join(alternatives) + ")")
    where.append("d.curated=1")
    if project:
        where.append("d.project=?")
        params.append(project)
    if category_key:
        where.append("d.category=?")
        params.append(category_key)
    if architecture_key == "other":
        where.append("(" + " OR ".join(
            "d.architectures LIKE ?" for _value in OTHER_ARCHITECTURES
        ) + ")")
        params.extend(f'%"{value}"%' for value in OTHER_ARCHITECTURES)
    elif architecture_key:
        where.append("d.architectures LIKE ?")
        params.append(f'%"{architecture_key}"%')
    where_sql = " AND ".join(where)
    total = int(conn.execute(
        f"SELECT COUNT(*) FROM {source_sql} WHERE {where_sql}", params,
    ).fetchone()[0])
    pages = math.ceil(total / per_page) if total else 0
    page = min(page, pages) if pages else 1
    exact = query.casefold()
    prefix = exact.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    order_sql = {
        "latest": "d.last_seen DESC,exact_rank,relevance,d.salience_score DESC",
        "oldest": "d.last_seen ASC,exact_rank,relevance,d.salience_score DESC",
    }.get(
        sort,
        "exact_rank,relevance,d.salience_score DESC,d.last_seen DESC",
    )
    rows = conn.execute(
        f"SELECT d.*,{relevance} AS relevance,"
        "CASE WHEN lower(d.original_title)=? OR lower(COALESCE(d.title_zh,''))=? THEN 0 "
        "WHEN lower(d.original_title) LIKE ? ESCAPE '\\' "
        "OR lower(COALESCE(d.title_zh,'')) LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END "
        f"AS exact_rank FROM {source_sql} WHERE {where_sql} "
        f"ORDER BY {order_sql} "
        "LIMIT ? OFFSET ?",
        [exact, exact, prefix, prefix, *params, per_page, (page - 1) * per_page],
    ).fetchall()
    highlight_terms = [query, *expansions]
    items = []
    for row in rows:
        item = dict(row)
        item["architectures"] = _json_list(item["architectures"])
        item["topics"] = _json_list(item["topics"])
        item["report_refs"] = _json_list(item["report_refs"])
        item["category_label"] = category.category_label(item["category"])
        item["kind_label"] = _KIND_LABELS.get(item["kind"], item["kind"] or "议题")
        item["date"] = (item["last_seen"] or item["first_seen"] or "")[:10]
        item["display_title"] = item["title_zh"] or item["original_title"]
        item["title_parts"] = _highlight_parts(item["display_title"], highlight_terms)
        item["original_title_parts"] = _highlight_parts(
            item["original_title"], highlight_terms
        )
        item["summary_parts"] = _highlight_parts(item["summary"], highlight_terms)
        items.append(item)
    base.update({
        "items": items, "total": total, "pages": pages, "page": page,
    })
    return base
