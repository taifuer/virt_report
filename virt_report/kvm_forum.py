"""KVM Forum 官方议程标题采集与年度主题分析。"""
from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from virt_report import __version__
from virt_report.config import Config
from virt_report.summarize import llm_provider

log = logging.getLogger(__name__)
CONTENT_DIR = Path(__file__).parent / "content"
TITLES_PATH = CONTENT_DIR / "kvm_forum_titles.json"
ANALYSIS_PATH = CONTENT_DIR / "kvm_forum_analysis.json"
PREVIEW_PATH = CONTENT_DIR / "kvm_forum_preview.json"
YEARS = range(2010, 2026)
WIKI_YEARS = {2011, 2012, 2013, 2014, 2015, 2017}
_NON_TOPIC_RE = re.compile(
    r"^(?:break|lunch|dinner|welcome|closing|keynote|hackathon|bofs?|lightning talks)$",
    re.IGNORECASE,
)


class _PresentationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.collecting = False
        self.in_heading = False
        self.heading_tag = ""
        self.heading: list[str] = []
        self.in_li = False
        self.in_italic = False
        self.li_text: list[str] = []
        self.italic_text: list[str] = []
        self.presentations: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"h1", "h2"}:
            self.in_heading = True
            self.heading_tag = tag
            self.heading = []
        elif self.collecting and tag == "li":
            self.in_li = True
            self.li_text = []
            self.italic_text = []
        elif self.in_li and tag in {"i", "em"}:
            self.in_italic = True

    def handle_data(self, data: str) -> None:
        if self.in_heading:
            self.heading.append(data)
        if self.in_li:
            self.li_text.append(data)
            if self.in_italic:
                self.italic_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"i", "em"}:
            self.in_italic = False
        elif tag == "li" and self.in_li:
            text = " ".join("".join(self.li_text).split())
            italic = " ".join("".join(self.italic_text).split())
            title = italic or re.sub(r"\s*\((?:slides?|video|pdf).*", "", text, flags=re.I)
            if title and not re.search(r"available|recordings?|live stream|schedule", title, re.I):
                self.presentations.append(title)
            self.in_li = False
        elif tag in {"h1", "h2"} and self.in_heading:
            text = " ".join("".join(self.heading).split()).lower()
            if "list of presentations" in text:
                self.collecting = True
            elif self.collecting and (
                self.heading_tag == "h1" or text in {"video", "videos", "pictures", "blogs"}
            ):
                self.collecting = False
            self.in_heading = False


def _clean_wiki_text(value: str) -> str:
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    if "|" in value and "=" in value.split("|", 1)[0]:
        value = value.split("|", 1)[1]
    value = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", value)
    value = re.sub(r"'{2,}", "", value)
    return " ".join(value.strip(" |\t").split())


def _wiki_link_labels(value: str) -> list[str]:
    """提取 MediaWiki 内外链的显示文字，不读取链接目标内容。"""
    labels = re.findall(r"\[\[[^\]|]+\|([^\]]+)\]\]", value)
    labels.extend(re.findall(r"\[https?://\S+\s+([^\]]+)\]", value))
    return [_clean_wiki_text(label) for label in labels if _clean_wiki_text(label)]


def _wiki_list_section(wikitext: str) -> str:
    """限制列表解析范围，避免把照片、博客等页面附录当作会议议题。"""
    match = re.search(
        r"^==+\s*(?:Videos and Slides|Presentations)\s*==+\s*$",
        wikitext, flags=re.I | re.M,
    )
    if not match:
        return ""
    tail = wikitext[match.end():]
    end = re.search(
        r"^==+\s*(?:Schedule|BoFs?|Community|Photos?|Pictures?|Blogs?|Notes)\b.*==+\s*$",
        tail, flags=re.I | re.M,
    )
    return tail[:end.start()] if end else tail


def _parse_wiki_titles(wikitext: str) -> list[str]:
    """从 linux-kvm 历史议程的 MediaWiki 表格/列表提取标题。"""
    titles: list[str] = []
    for table in re.findall(r"\{\|(.*?)\|\}", wikitext, flags=re.S):
        header = next(
            (line for line in reversed(table.splitlines())
             if line.startswith("!") and "time" in line.lower()
             and "title" in line.lower()),
            "",
        )
        headings = [_clean_wiki_text(cell).lower()
                    for cell in header.lstrip("!").split("!!")]
        title_columns = [index for index, value in enumerate(headings)
                         if value == "title"]
        for row in re.split(r"\n\|-.*\n", table):
            raw_cells: list[str] = []
            for line in row.splitlines():
                if line.startswith("|") and not line.startswith("|-"):
                    raw_cells.extend(line.lstrip("|").split("||"))
            cells = [_clean_wiki_text(cell) for cell in raw_cells]
            if not cells or not re.search(r"\d{1,2}:\d{2}", cells[0]):
                continue
            # 议程为 Time, Title, Speaker[, Title, Speaker]。
            columns = list(title_columns or range(1, len(cells), 2))
            # 个别旧议程的三会场表头只写了两组 Title/Speaker，但数据行仍有
            # 第三组；从已声明表头之后继续按 Title/Speaker 成对补齐。
            if headings and len(cells) > len(headings):
                columns.extend(range(len(headings), len(cells), 2))
            for index in columns:
                if index >= len(cells):
                    continue
                raw_title = raw_cells[index]
                title = cells[index]
                if title.lower().startswith("lightning talks:"):
                    titles.extend(label for label in _wiki_link_labels(raw_title)
                                  if not _NON_TOPIC_RE.fullmatch(label))
                elif title.lower() == "lightning talks":
                    lightning = [
                        _clean_wiki_text(line.lstrip("*# "))
                        for line in row.splitlines()
                        if line.lstrip().startswith(("*", "#"))
                    ]
                    titles.extend(label for label in lightning
                                  if label and not _NON_TOPIC_RE.fullmatch(label))
                elif title and not _NON_TOPIC_RE.fullmatch(title):
                    titles.append(title)
    if not titles:
        # 2015/2017 使用无表格的列表；优先取条目中的链接显示文字。
        for line in _wiki_list_section(wikitext).splitlines():
            if not line.lstrip().startswith(("*", "#")):
                continue
            text = _clean_wiki_text(line.lstrip("*# "))
            text = re.sub(r"\s*\((?:video|slides?).*$", "", text, flags=re.I)
            text = re.sub(r"\s+by\s+[A-Z][\s\S]*$", "", text)
            if " - " in text:
                prefix, suffix = text.rsplit(" - ", 1)
                if "," in suffix or " & " in suffix or "/" in suffix:
                    text = prefix
            if (len(text) >= 8 and not _NON_TOPIC_RE.fullmatch(text)
                    and not re.search(r"registration|schedule|slides and video", text, re.I)):
                titles.append(text)
    return list(dict.fromkeys(titles))


def _fetch_wiki_titles(year: int) -> tuple[list[str], str]:
    params = urlencode({
        "action": "parse", "page": f"KVM Forum {year}",
        "prop": "wikitext", "format": "json",
    })
    api_url = f"https://www.linux-kvm.org/api.php?{params}"
    request = Request(api_url, headers={
        "User-Agent": f"virt-report/{__version__}",
    })
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    wikitext = payload["parse"]["wikitext"]["*"]
    return _parse_wiki_titles(wikitext), f"https://www.linux-kvm.org/page/KVM_Forum_{year}"


def fetch_titles() -> list[dict]:
    """从 KVM Forum 官方页提取标题，旧版缺失年份回退到 KVM 项目 Wiki。"""
    editions = []
    for year in YEARS:
        url = f"https://kvm-forum.qemu.org/{year}/"
        request = Request(url, headers={
            "User-Agent": f"virt-report/{__version__}",
        })
        with urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="replace")
        parser = _PresentationParser()
        parser.feed(html)
        titles = list(dict.fromkeys(
            title for title in parser.presentations
            if not _NON_TOPIC_RE.fullmatch(title)
        ))
        source_url = url
        # 旧版官网的迁移页面有缺项；这些年份以 KVM Wiki 的完整议程为准。
        if year in WIKI_YEARS:
            titles, source_url = _fetch_wiki_titles(year)
        editions.append({"year": year, "url": source_url, "titles": titles})
        log.info("KVM Forum %d: %d 个议题", year, len(titles))
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    TITLES_PATH.write_text(json.dumps({"editions": editions}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return editions


def analyze(config: Config, editions: list[dict]) -> dict:
    """使用周报级 Pro 模型分析年度主题，不读取幻灯片正文。"""
    provider = llm_provider.get_provider(config.llm)
    if not provider:
        raise RuntimeError(f"未配置 {config.llm.api_key_env}，无法生成 KVM Forum 分析")
    evidence = "\n\n".join(
        f"## {edition['year']} ({len(edition['titles'])} talks)\n" +
        "\n".join(f"- {title}" for title in edition["titles"])
        for edition in editions
    )
    prompt = f"""仅根据以下 KVM Forum 官方年度议程页列出的议题名称，分析 2010—2025 年技术主题演进。
不要推断幻灯片中的具体结论，不要把议题名称未体现的信息写成事实。议题为空或明显不完整的年份必须说明样本不足。

返回 JSON：
{{
  "headline":"一句总趋势",
  "overview":"200-350字长期演进总结",
  "eras":[{{"years":"2010—2013","name":"阶段名","summary":"阶段特征"}}],
  "years":[{{"year":2010,"headline":"年度主题判断","themes":["主题1","主题2","主题3"],"summary":"80-140字，只基于议题名称"}}]
}}

要求：years 必须覆盖 2010 至 2025 共 16 年；themes 每年 2-5 个；使用简洁、克制的中文。

议题数据：
{evidence}"""
    text = provider.complete(
        prompt,
        system="你是 Linux 虚拟化社区技术史分析员，只能依据提供的议题名称做克制归纳。",
        model=config.llm.weekly_model,
        max_tokens=20000,
        json_mode=True,
        thinking="enabled",
        reasoning_effort="high",
    )
    result = llm_provider.extract_json(text)
    if not result:
        raise RuntimeError("KVM Forum AI 分析返回不可解析内容")
    result.update({
        "model": config.llm.weekly_model,
        "source": "https://kvm-forum.qemu.org/archive/",
        "method": "仅依据各年度官方议程页的议题名称进行 AI 辅助归纳，不读取 PPT 或视频；结论不代表演讲全文内容。",
        "usage": getattr(provider, "last_usage", {}),
    })
    ANALYSIS_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load_content() -> tuple[list[dict], dict]:
    """读取历史点评及独立维护的会前预览，不在页面请求中采集或调用模型。"""
    titles = json.loads(TITLES_PATH.read_text(encoding="utf-8"))["editions"]
    analysis = json.loads(ANALYSIS_PATH.read_text(encoding="utf-8"))
    by_year = {item["year"]: item for item in analysis.get("years", [])}
    for edition in titles:
        edition["analysis"] = by_year.get(edition["year"], {})
    if PREVIEW_PATH.exists():
        preview = json.loads(PREVIEW_PATH.read_text(encoding="utf-8"))
        if preview["year"] not in {item["year"] for item in titles}:
            preview["titles"] = [item["title"] for item in preview["talks"]]
            preview["selected_talks"] = [item for item in preview["talks"]
                                         if item["url"] in preview["selected_urls"]]
            titles.append(preview)
    titles.sort(key=lambda item: item["year"])
    return titles, analysis


def load_titles() -> list[dict]:
    """读取已保存的标题证据，不要求分析文件已经存在。"""
    return json.loads(TITLES_PATH.read_text(encoding="utf-8"))["editions"]
