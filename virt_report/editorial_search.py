"""Small offline search catalogue for source-reviewed articles and walkthroughs."""
from __future__ import annotations

from functools import lru_cache

from virt_report import mechanisms, search


@lru_cache(maxsize=1)
def catalogue() -> dict:
    """Index published metadata and curated terms, never redirects or private data."""
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
    return {"documents": documents, "aliases": search._QUERY_ALIASES}


def find(query: str) -> dict:
    """Match all terms, with the same aliases and weights as the static browser."""
    query = search._clean(query)
    result = {"query": query, "articles": [], "demos": [], "error": ""}
    if not query:
        return result
    if len(query) < 2 or len(query) > 100:
        result["error"] = "请至少输入 2 个字符。" if len(query) < 2 else "搜索关键词请控制在 100 个字符以内。"
        return result
    query_terms = list(dict.fromkeys(query.lower().split()))[:8]
    groups = [[term, *catalogue()["aliases"].get(term, ())] for term in query_terms]
    terms = [term for group in groups for term in group]
    matches = []
    for document in catalogue()["documents"]:
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
