"""The industry desk has its own evidence, publication boundary and weekly clock."""
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from virt_report import cli, industry, industry_watch as watch, scheduler, server
from virt_report.config import Config, Render, Storage
from virt_report.render import render
from virt_report.versions import Document


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(industry, "BUNDLED", tmp_path / "bundled")
    return Config(storage=Storage(db_path=tmp_path / "data" / "test.db"),
                  render=Render(output_dir=tmp_path / "site"))


def issue(week="2025-W01", event="device-preview"):
    start, end = industry.week_dates(week)
    source = dict(title="Original", url="https://vendor.example/release", date=start.isoformat(),
                  publisher="设备厂商")
    return dict(week=week, published_on=date.fromordinal(end.toordinal()+1).isoformat(),
                lead="本周概括", sources={"a": source},
                points=[dict(event=event, date=end.isoformat(), title="设备预览", refs=["a"],
                             paragraphs=[dict(text="读取 `DMA` 完成时间。<script>bad()</script>", refs=["a"])])])


def test_previous_week_and_monday_schedule_use_beijing_even_with_utc_host():
    assert industry.week_dates("2025-W01") == (date(2024, 12, 30), date(2025, 1, 5))
    assert industry.previous_week(datetime(2025, 1, 5, 15, 59, tzinfo=timezone.utc)) == "2024-W52"
    assert industry.previous_week(datetime(2025, 1, 5, 16, 0, tzinfo=timezone.utc)) == "2025-W01"
    cfg = Config(timezone="UTC")
    monday = datetime(2025, 1, 6, 1, tzinfo=timezone.utc)
    assert ("industry", ["industry-draft", "2025-W01"]) in scheduler.scheduled_commands(cfg, monday)
    cfg.schedule.industry_draft_ai = True
    assert ("industry", ["industry-draft", "2025-W01", "--ai"]) in scheduler.scheduled_commands(cfg, monday)
    assert "industry" not in scheduler.REPORT_JOBS
    cfg.schedule.industry_cron = ""
    assert not any(k == "industry" for k, _ in scheduler.scheduled_commands(cfg, monday))


@pytest.mark.parametrize("change", [
    lambda r: r.update(week="2025-W54"),
    lambda r: r["points"][0].update(date="2025-01-06"),
    lambda r: r["points"][0].update(refs=[]),
    lambda r: r["points"][0].update(refs=["nonexistent"]),
    lambda r: r["points"][0]["paragraphs"][0].update(refs=["a", "a"]),
    lambda r: r["sources"]["a"].update(url="javascript:wrong"),
    lambda r: r["sources"]["a"].update(date="2025-02-01"),
    lambda r: r.update(published_on="2025-01-05"),
    lambda r: r["sources"].update(unused=r["sources"]["a"]),
    lambda r: r.update(points=r["points"] * 11),
])
def test_publication_rejects_bad_dates_missing_evidence_and_overflow(change):
    raw = issue()
    change(raw)
    with pytest.raises(ValueError):
        industry.prepare(raw)


def test_references_are_first_cited_shared_and_directly_clickable(config):
    raw = issue()
    raw["points"][0]["refs"] = ["b"]
    raw["sources"]["b"] = dict(raw["sources"]["a"], url="https://other.example/source")
    ready = industry.prepare(raw)
    assert [s["url"] for s in ready["sources"]] == ["https://other.example/source", "https://vendor.example/release"]
    page = render.render_industry_html(config, editions=[ready])
    article, = Document(page).root.find("article")
    links = [a for a in article.find("a") if a.attrs.get("class") == "industry-ref"]
    assert [(a.text(), a.attrs["href"]) for a in links] == [(f"[{s['number']}]", s["url"]) for s in ready["sources"]]
    sources, = article.find("section")
    originals = sources.find("a")
    assert [(a.text(), a.attrs["href"]) for a in originals] == [(s["title"], s["url"]) for s in ready["sources"]]
    for link in links + originals:
        assert link.attrs.get("target") == "_blank"
        assert {"noopener", "noreferrer"} <= set(link.attrs["rel"].split())
    assert "<code>DMA</code>" in page and "&lt;script&gt;bad()" in page
    raw["sources"]["b"]["url"] = raw["sources"]["a"]["url"]
    with pytest.raises(ValueError, match="重复"):
        industry.prepare(raw)


def test_only_reviewed_editions_are_exported_and_retry_is_safe(config):
    raw = issue()
    draft = industry.store_dir(config) / "drafts/2025-W02/brief.json"
    industry.write_json(draft, dict(issue("2025-W02"), lead="私有未发布"))
    assert industry.issues(config) == []
    assert industry.publish(config, raw) == industry.publish(config, deepcopy(raw)) == "2025-W01"
    with pytest.raises(ValueError, match="已发布"):
        industry.publish(config, dict(raw, lead="不同的终稿"))
    with pytest.raises(ValueError, match="往期"):
        industry.publish(config, issue("2025-W02"))
    empty = dict(issue("2025-W02"), lead="本周未发现重要进展。", points=[], sources={})
    industry.publish(config, empty)
    paths = render.render_industry(config)
    assert {p.name for p in paths} == {"index.html", "archive.html", "2025-W01.html", "2025-W02.html"}
    pages = {p.stem: p.read_text() for p in paths}
    assert pages["index"] == pages["2025-W02"] and empty["lead"] in pages["index"]
    assert 'rel="prev" href="2025-W01.html"' in pages["index"] and 'rel="next"' not in pages["index"]
    assert pages["2025-W01"] == render.render_industry_html(config, "2025-W01")
    assert pages["archive"].index("2025-W02.html") < pages["archive"].index("2025-W01.html")
    assert all("私有未发布" not in html for html in pages.values())
    assert (config.output_dir / "assets/industry.css").is_file() and not config.db_path.exists()


def test_issue_navigation_and_legacy_editorial_fields(config):
    raw = issue()
    legacy = dict(raw, follow_up="旧稿的编辑建议")
    path = industry.store_dir(config) / "published" / f"{raw['week']}.json"
    industry.write_json(path, legacy)
    assert industry.prepare(legacy) == industry.prepare(raw)
    assert industry.publish(config, raw) == raw["week"]
    for page, destination, label in [("index", "archive.html", "往期报告"),
                                     (raw["week"], "archive.html", "往期报告"),
                                     ("archive", "index.html", "最新一期")]:
        html = render.render_industry_html(config, page)
        article, = Document(html).root.find("article")
        header, = article.find("header")
        row, = [div for div in header.find("div") if div.attrs.get("class") == "industry-title-row"]
        title, = row.find("h1")
        assert title.text() == ("往期产业周报" if page == "archive" else "产业周报")
        link, = row.find("a")
        assert (link.attrs["href"], link.text()) == (destination, label)
        assert "target" not in link.attrs
        assert not row.find("time")
        assert not article.find("footer")
        assert not article.find("nav")  # One published issue has no neighbours.
        assert all(removed not in html for removed in ("北京时间", "值得跟进", "旧稿的编辑建议", "本期固定链接"))
        if page == "archive":
            editions, = article.find("ul")
            edition, = editions.find("a")
            assert edition.attrs["href"] == f"{raw['week']}.html" and "target" not in edition.attrs
            stamp, = edition.find("time")
        else:
            stamp, = header.find("time")
            assert stamp.text() in Document(html).root.find("title")[0].text()
            assert 'class="industry-sources"' in html
        assert (stamp.attrs["datetime"], stamp.text()) == ("2025-W01", "2025 年第 1 周（12.30–1.5）")


def test_pager_follows_published_weeks_across_gaps_and_iso_years(config):
    assert 'class="industry-issue-nav"' not in render.render_industry_html(config)
    old, middle, new = "2020-W53", "2021-W02", "2021-W04"
    for key in (new, old, middle):
        industry.publish(config, issue(key, event="update-" + key.lower()))
    industry.write_json(industry.store_dir(config) / "drafts/2021-W03/brief.json", issue("2021-W03"))
    pages = {p.stem: p.read_text() for p in render.render_industry(config)}
    assert 'class="industry-issue-nav"' not in pages["archive"]
    for page, prev, nxt in [("index", middle, None), (new, middle, None),
                            (middle, old, new), (old, None, middle)]:
        html = pages[page]
        assert html == render.render_industry_html(config, page)
        assert html.index('class="industry-points"') < html.index('class="industry-issue-nav"') < html.index('class="industry-sources"')
        article, = Document(html).root.find("article")
        nav, = article.find("nav")
        links = nav.find("a")
        assert [(a.attrs["rel"], a.attrs["href"]) for a in links] == [
            (direction, f"{key}.html") for direction, key in (("prev", prev), ("next", nxt)) if key]
        assert all("target" not in a.attrs for a in links)
    assert "2020 年第 53 周（12.28–1.3）" in pages[old]


def test_web_routes_use_only_the_published_store(config):
    industry.publish(config, issue())
    handler = object.__new__(server.make_handler(config))
    handler.headers = {}
    results = []
    handler._send = lambda status, body, *args, **kw: results.append((status, body, kw))
    for path, code in [("/industry", 308), ("/industry/", 200), ("/industry/2025-W01.html", 200),
                       ("/industry/archive.html", 200), ("/industry/2025-W06.html", 404),
                       ("/industry/drafts/brief.json", 404), ("/assets/industry.css", 200)]:
        handler.path = path
        handler._dispatch(head_only=False)
        assert results[-1][0] == code, path
    assert results[0][2]["extra_headers"]["Location"] == "/industry/"
    assert results[1][1] == results[2][1] and not config.db_path.exists()


def test_public_navigation_keeps_the_mechanism_directory_in_about():
    cfg = Config()
    for page, current in [(render.render_industry_html(cfg), "产业"), (render.render_about_html(cfg), "关于"),
                          (render.render_mechanisms_html(cfg), None)]:
        nav = next(n for n in Document(page).root.find("nav") if n.attrs.get("class") == "main-nav")
        assert "产业" in [a.text() for a in nav.find("a")]
        assert "原理" not in [a.text() for a in nav.find("a")]
        assert [a.text() for a in nav.find("a") if a.attrs.get("aria-current")] == ([current] if current else [])
    assert 'href="mechanisms.html">虚拟化原理文章</a>' in render.render_about_html(cfg)


def test_feed_filter_respects_midnight_and_records_a_short_feed_or_failure():
    src = watch.Source("Cloud", "https://cloud.example/feed")
    bad = watch.Source("Offline", "https://offline.example/news")
    def fetch(source, url):
        if source == bad:
            raise ValueError("offline")
        if url.endswith("/feed"):
            items = "".join(f"<item><title>{name}</title><link>https://cloud.example/{n}</link>"
                            f"<pubDate>{stamp} GMT</pubDate></item>" for n, (name, stamp) in enumerate([
                                ("VFIO preview", "Sun, 05 Jan 2025 15:59:00"),
                                ("KVM outside", "Sun, 05 Jan 2025 16:00:00"),
                                ("VMs now available in region A", "Fri, 03 Jan 2025 09:30:00")]))
            return f'<rss version="2.0"><channel>{items}</channel></rss>'.encode()
        return ("<main><p>" + "Documented device support. " * 10 + "</p></main>").encode()
    out = watch.research("2025-W01", [], sources=[src, bad], fetch=fetch)
    assert [s["status"] for s in out["sources"]] == ["short-window", "failed"]
    assert [(c["title"], c["date"], c["evidence"]) for c in out["candidates"]] == [
        ("VFIO preview", "2025-01-05", "原文节选")]
    assert not src.allows("https://cloud.example.evil.org/")
    assert not src.allows("https://user@cloud.example/") and not src.allows("http://cloud.example/")


def test_month_only_index_does_not_assign_an_event_to_arbitrary_week():
    src = watch.Source("Cloud", "https://cloud.example/news", "table")
    def fetch(source, url):
        return "<h2>2025年01月</h2><table><tr><td>VFIO</td><td>2025-01</td></tr></table>".encode()
    result = watch.research("2025-W01", [], sources=[src], fetch=fetch)
    assert not result["candidates"] and result["sources"][0]["status"] == "undated"
    assert result["sources"][0]["month_only_leads"][0]["title"] == "VFIO"
    assert "待查事件日：2025-01 · VFIO" in watch.review_sheet(result)
    rows, _ = watch._table_entries(src, Document("<table><tr><td>VFIO</td><td>2025-01-03</td></tr></table>").root, set())
    assert rows[0][:4] == (date(2025, 1, 3), "公告日期", "VFIO", src.url)


def test_dated_release_section_and_tencent_product_panel():
    s = watch.Source("Cloud", "https://cloud.example/news", "table")
    rows, pending = watch._table_entries(s, Document(
        "<h4>2025年01月</h4><p>2025年01月03日</p><table><tr><td>1</td><td>VFIO</td></tr></table>"
        "<h4>2024年12月</h4><table><tr><td>QEMU</td></tr></table>").root, {"2024-12"})
    assert [r[0] for r in rows] == [date(2025, 1, 3)]
    assert [r["title"] for r in pending] == ["QEMU"]
    s = watch.Source("Cloud", "https://cloud.example/events", "events")
    src = b'<div class="pr2-product-panel__inner"><h3>VM live migration</h3><div>2025-01-01</div></div>'
    assert watch._entries(s, src)[0][:3] == (date(2025, 1, 1), "公告日期", "VM live migration")


def test_draft_refresh_preserves_editorial_copy_and_requires_explicit_publish(config, monkeypatch):
    material = dict(week="2025-W01", start="2024-12-30", end="2025-01-05", candidates=[], recent_events=[],
                    sources=[dict(status="failed", publisher="Cloud", url="https://cloud.example")])
    monkeypatch.setattr(watch, "research", lambda *args: material)
    base, partial = watch.collect_draft(config, "2025-W01")
    assert partial and json.loads((base / "research.json").read_text()) == material
    assert "访问失败" in (base / "review.txt").read_text()
    with pytest.raises(ValueError):
        industry.prepare(json.loads((base / "brief.json").read_text()))
    raw = issue()
    industry.write_json(base / "brief.json", raw)
    watch.collect_draft(config, "2025-W01")
    assert json.loads((base / "brief.json").read_text()) == raw
    with pytest.raises(SystemExit):
        cli.main(["industry-publish", str(base / "brief.json")])
    assert not industry.issues(config)
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    assert cli.main(["industry-publish", str(base / "brief.json"), "--reviewed"]) == 0
    assert (config.output_dir / "industry/2025-W01.html").exists()


def test_model_proposal_is_bounded_to_originals_and_never_self_publishes(config, monkeypatch):
    from virt_report.summarize import llm_provider

    raw = issue()
    material = dict(week=raw["week"], start="2024-12-30", end="2025-01-05", sources=[], recent_events=[],
                    candidates=[dict(raw["sources"]["a"], id="a", evidence="原文节选", excerpt="原始资料",
                                     previously_cited_url=False, date_basis="发布日期", source_date=raw["sources"]["a"]["date"])])
    result = deepcopy(raw)
    result["follow_up"] = "旧版生成器的建议"
    result["sources"]["a"]["url"] = "https://made-up.example/news"
    class Provider:
        def complete(self, prompt, **kwargs):
            assert "原始资料" in prompt and kwargs["json_mode"]
            return json.dumps(result)
    monkeypatch.setattr(watch, "research", lambda *args: material)
    monkeypatch.setattr(llm_provider, "get_provider", lambda *_: Provider())
    base, _ = watch.collect_draft(config, raw["week"], ai=True)
    proposal = json.loads((base / "proposal.json").read_text())
    assert proposal["sources"] == raw["sources"] and not industry.issues(config)
    assert "follow_up" not in proposal
    result["points"][0]["refs"] = ["invented"]
    with pytest.raises(ValueError, match="候选之外"):
        watch._propose(config, material, raw)
    # A retry cannot call the model over an existing proposal or edited copy.
    assert watch.collect_draft(config, raw["week"], ai=True)[0] == base


def test_feed_does_not_follow_third_party_redirects(monkeypatch):
    src = watch.Source("Cloud", "https://cloud.example/feed")
    calls = []
    class Response:
        is_redirect = True
        headers = {"Location": "https://other.example/feed"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    def get(url, **kwargs):
        calls.append(url)
        assert not kwargs["allow_redirects"]
        return Response()
    monkeypatch.setattr(watch.requests, "get", get)
    with pytest.raises(ValueError, match="域名"):
        watch.download(src, src.url)
    assert calls == [src.url]


def test_recent_update_does_not_make_an_old_article_a_new_release(config, monkeypatch):
    src = watch.Source("Cloud", "https://cloud.example/atom")
    def fetch(source, url):
        if url == src.url:
            return b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>QEMU update</title>
                <link href="https://cloud.example/article"/><updated>2025-01-04T15:00:00Z</updated></entry></feed>'''
        return ('<meta property="article:published_time" content="2024-10-11T23:00:00Z"><article><p>'
                + 'Older results. ' * 15 + '</p></article>').encode()
    material = watch.research("2025-W01", [], sources=[src], fetch=fetch)
    candidate, = material["candidates"]
    assert candidate["date"] == "2025-01-04" and candidate["source_date"] == "2024-10-12"
    from virt_report.summarize import llm_provider
    monkeypatch.setattr(llm_provider, "get_provider", lambda *_: object())
    with pytest.raises(ValueError, match="没有已读取原文"):
        watch._propose(config, material, {})


@pytest.mark.parametrize("week", ["2026-W44", "2026-W45", "2026-W46", "2026-W47"])
def test_bundled_edition_and_static_snapshot_agree(week):
    cfg = Config()
    editions = [industry.prepare(json.loads(p.read_text())) for p in industry.BUNDLED.glob("*.json")]
    ready = next(ed for ed in editions if ed["week"] == week)
    html = render.render_industry_html(cfg, week, editions=editions)
    assert (Path(__file__).parents[1] / "site" / "industry" / f"{week}.html").read_text() == html
    links = [a for a in Document(html).root.find("a") if a.attrs.get("class") == "industry-ref"]
    assert {a.attrs["href"] for a in links} == {s["url"] for s in ready["sources"]}
    if week == "2026-W47":
        assert {"cloud-hypervisor-55", "azure-boost-guest-rdma-all-regions-preview"} <= {
            p["event"] for p in ready["points"]}


def test_industry_backfill_covers_four_weeks_without_repeating_events(config, monkeypatch):
    bundled = Path(__file__).parents[1] / "virt_report" / "content" / "industry"
    monkeypatch.setattr(industry, "BUNDLED", bundled)
    weeks = ["2026-W47", "2026-W46", "2026-W45", "2026-W44"]
    editions = [issue for issue in industry.issues(config) if issue["week"] in weeks]
    assert [issue["week"] for issue in editions] == weeks
    events = [p["event"] for issue in editions for p in issue["points"]]
    assert len(set(events)) == len(events)
    archive = Document(render.render_industry_html(config, "archive", editions=editions))
    assert [a.attrs["href"] for a in archive.root.find("a")
            if a.attrs.get("href") in {w + ".html" for w in weeks}] == [w + ".html" for w in weeks]
