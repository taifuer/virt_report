"""Offline search for articles, walkthroughs, reviewed papers and release notes."""
from __future__ import annotations

from functools import lru_cache

from virt_report import conferences, mechanisms, search, versions
from virt_report.config import Config


@lru_cache(maxsize=1)
def _editorial_documents() -> list[dict]:
    """Cache committed content only; runtime release snapshots remain refreshable."""
    from virt_report.execution_demos import get_demo

    documents = []
    for article in mechanisms.articles():
        headings = [section["title"] for section in article["sections"]]
        if article.get("execution_demo"):
            demo = get_demo(article["execution_demo"])
            headings.extend(scene["label"] for scene in demo["scenarios"])
        documents.append({
            "slug": article["slug"], "title": article["title"],
            "summary": article["summary"],
            "kind": "demos" if article.get("presentation") == "walkthrough" else "articles",
            "href": f'mechanisms/{article["slug"]}.html',
            "headings": list(dict.fromkeys(headings)),
            "keywords": " ".join([
                article["slug"], article["topic"], *article.get("search_terms", []),
            ]),
        })
    for paper in conferences.load_content()["papers"]:
        documents.append({
            "slug": paper["id"], "title": paper["title"],
            "summary": paper["introduction"], "kind": "papers",
            "href": f'conference-papers.html#paper-{paper["id"]}',
            "headings": paper["topics"], "heading_label": "匹配主题",
            "meta": f'{paper["venue_name"]} · {paper["year"]}',
            "keywords": " ".join([paper["commentary"], *paper.get("institutions", []),
                                  *paper.get("architectures", []), paper["id"],
                                  paper["venue_name"], str(paper["year"])]),
        })
    return documents


def catalogue(version_content: dict | None = None) -> dict:
    """Use the same release snapshot as the timeline, without network access."""
    content = version_content if version_content is not None else versions.load_content(Config())
    documents = list(_editorial_documents())
    for release in content["releases"]:
        if release["kind"] != "feature":
            continue
        project, version = release["project"], release["version"]
        documents.append({
            "slug": f"release-{project}-{version}", "kind": "versions",
            "title": f'{release["project_label"]} {"Linux " if project == "kvm" else ""}{version}',
            "summary": "；".join(release.get("notes", [])) or "正式功能版本；发布来源见版本详情。",
            "href": f"versions.html?project={project}&view=detailed#release-{project}-{version}",
            "headings": [content["topics"][key]["label"] for key in release.get("topics", [])],
            "heading_label": "匹配主题", "meta": release["released_on"],
            "keywords": f'{project} {version} {release["released_on"]}',
        })
    for spec in content.get("specifications", []):
        version, stage = spec["version"], spec["stage"]
        documents.append({
            "slug": f"release-virtio-{version}-{stage.lower()}", "kind": "versions",
            "title": f"Virtio {version} {stage}",
            "summary": "；".join(spec.get("notes", [])) or spec["status"],
            "href": f"versions.html?project=virtio&view=detailed#release-virtio-{version}-{stage.lower()}",
            "headings": [], "heading_label": "匹配主题",
            "meta": f'{spec["released_on"]} · {spec["status"]}',
            "keywords": f'virtio 协议 规范 {version} {stage} {spec["status"]}',
        })
    return {"documents": documents, "aliases": search._QUERY_ALIASES}


def find(query: str, index: dict | None = None) -> dict:
    """Match all terms, with the same aliases and weights as the static browser."""
    query = search._clean(query)
    result = {"query": query, "articles": [], "demos": [], "papers": [], "versions": [], "error": ""}
    if not query:
        return result
    if len(query) < 2 or len(query) > 100:
        result["error"] = "请至少输入 2 个字符。" if len(query) < 2 else "搜索关键词请控制在 100 个字符以内。"
        return result
    query_terms = list(dict.fromkeys(query.lower().split()))[:8]
    index = index if index is not None else catalogue()
    groups = [[term, *index["aliases"].get(term, ())] for term in query_terms]
    terms = [term for group in groups for term in group]
    matches = []
    for document in index["documents"]:
        fields = [(document["title"].lower(), 8),
                  (document["summary"].lower(), 4),
                  (" ".join(document["headings"]).lower(), 2),
                  (document["keywords"].lower(), 1)]
        scores = [max((weight for text, weight in fields
                       if any(term in text for term in group)), default=0)
                  for group in groups]
        if not all(scores):
            continue
        matched_headings = [heading for heading in document["headings"]
                            if any(term in heading.lower() for term in terms)]
        matches.append((sum(scores), dict(document, matched_headings=matched_headings[:2])))
    # Stable ties preserve the curated catalogue order in both runtimes.
    for _score, item in sorted(matches, key=lambda match: -match[0]):
        result[item["kind"]].append(item)
    return result
