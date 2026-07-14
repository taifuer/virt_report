"""KVM Forum 官方议程标题采集与年度主题分析。"""
from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from virt_report.config import Config
from virt_report.summarize import llm_provider

log = logging.getLogger(__name__)
CONTENT_DIR = Path(__file__).parent / "content"
TITLES_PATH = CONTENT_DIR / "kvm_forum_titles.json"
ANALYSIS_PATH = CONTENT_DIR / "kvm_forum_analysis.json"
YEARS = range(2010, 2026)
WIKI_YEARS = {2012, 2013, 2014, 2015, 2017}


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
    value = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", value)
    value = re.sub(r"'{2,}", "", value)
    return " ".join(value.strip(" |\t").split())


def _parse_wiki_titles(wikitext: str) -> list[str]:
    """从 linux-kvm 历史议程的 MediaWiki 表格/列表提取标题。"""
    titles: list[str] = []
    for table in re.findall(r"\{\|(.*?)\|\}", wikitext, flags=re.S):
        for row in re.split(r"\n\|-.*\n", table):
            cells: list[str] = []
            for line in row.splitlines():
                if line.startswith("|") and not line.startswith("|-"):
                    cells.extend(line.lstrip("|").split("||"))
            cells = [_clean_wiki_text(cell) for cell in cells]
            if not cells or not re.search(r"\d{1,2}:\d{2}", cells[0]):
                continue
            # 议程为 Time, Title, Speaker[, Title, Speaker]。
            for index in range(1, len(cells), 2):
                title = cells[index]
                if title and not re.fullmatch(r"(?:break|lunch|dinner|keynote)", title, re.I):
                    titles.append(title)
    if not titles:
        # 2015/2017 使用无表格的列表；优先取条目中的链接显示文字。
        for line in wikitext.splitlines():
            if not line.lstrip().startswith(("*", "#")):
                continue
            text = _clean_wiki_text(line.lstrip("*# "))
            text = re.sub(r"\s*\((?:video|slides?).*$", "", text, flags=re.I)
            text = re.sub(r"\s+by\s+[A-Z][\s\S]*$", "", text)
            if " - " in text:
                prefix, suffix = text.rsplit(" - ", 1)
                if "," in suffix or " & " in suffix or "/" in suffix:
                    text = prefix
            if len(text) >= 8 and not re.search(r"registration|schedule|slides and video", text, re.I):
                titles.append(text)
    return list(dict.fromkeys(titles))


def _fetch_wiki_titles(year: int) -> tuple[list[str], str]:
    params = urlencode({
        "action": "parse", "page": f"KVM Forum {year}",
        "prop": "wikitext", "format": "json",
    })
    api_url = f"https://www.linux-kvm.org/api.php?{params}"
    request = Request(api_url, headers={"User-Agent": "virt-report/0.1"})
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    wikitext = payload["parse"]["wikitext"]["*"]
    return _parse_wiki_titles(wikitext), f"https://www.linux-kvm.org/page/KVM_Forum_{year}"


def fetch_titles() -> list[dict]:
    """从 KVM Forum 官方页提取标题，旧版缺失年份回退到 KVM 项目 Wiki。"""
    editions = []
    for year in YEARS:
        url = f"https://kvm-forum.qemu.org/{year}/"
        request = Request(url, headers={"User-Agent": "virt-report/0.1"})
        with urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="replace")
        parser = _PresentationParser()
        parser.feed(html)
        titles = list(dict.fromkeys(parser.presentations))
        source_url = url
        if not titles and year in WIKI_YEARS:
            titles, source_url = _fetch_wiki_titles(year)
        editions.append({"year": year, "url": source_url, "titles": titles})
        log.info("KVM Forum %d: %d 个标题", year, len(titles))
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
    prompt = f"""仅根据以下 KVM Forum 官方年度页面中的演讲标题，分析 2010—2025 年研究主题演进。
不要推断幻灯片中的具体结论，不要把标题未体现的信息写成事实。标题为空或明显不完整的年份必须说明样本不足。

返回 JSON：
{{
  "headline":"一句总趋势",
  "overview":"200-350字长期演进总结",
  "eras":[{{"years":"2010—2013","name":"阶段名","summary":"阶段特征"}}],
  "years":[{{"year":2010,"headline":"年度主题判断","themes":["主题1","主题2","主题3"],"summary":"80-140字，只基于标题"}}]
}}

要求：years 必须覆盖 2010 至 2025 共 16 年；themes 每年 2-5 个；使用简洁、克制的中文。

标题数据：
{evidence}"""
    text = provider.complete(
        prompt,
        system="你是 Linux 虚拟化社区技术史分析员，只能依据提供的演讲标题归纳。",
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
        "method": "仅分析各年度官方页面列出的演讲标题，不读取 PPT 或视频内容。",
        "usage": getattr(provider, "last_usage", {}),
    })
    ANALYSIS_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load_content() -> tuple[list[dict], dict]:
    """读取已提交的标题证据与 AI 分析。"""
    titles = json.loads(TITLES_PATH.read_text(encoding="utf-8"))["editions"]
    analysis = json.loads(ANALYSIS_PATH.read_text(encoding="utf-8"))
    by_year = {item["year"]: item for item in analysis.get("years", [])}
    for edition in titles:
        edition["analysis"] = by_year.get(edition["year"], {})
    return titles, analysis


def load_titles() -> list[dict]:
    """读取已保存的标题证据，不要求分析文件已经存在。"""
    return json.loads(TITLES_PATH.read_text(encoding="utf-8"))["editions"]
