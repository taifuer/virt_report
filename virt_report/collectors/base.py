"""采集器公共工具：HTTP、时间解析、邮件主题分类、正文摘要。"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

import requests

# lore.kernel.org 对默认 curl UA 返回 403，用浏览器 UA
HTTP_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30


def http_get(url: str, *, params: dict | None = None, headers: dict | None = None,
             retries: int = 3) -> requests.Response:
    """带退避重试的 GET。429/5xx 退避，其余直接抛出。"""
    hdrs = {"User-Agent": HTTP_UA, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    last_exc: Exception | None = None
    last_resp: requests.Response | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=DEFAULT_TIMEOUT)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_resp = resp
                if attempt < retries - 1:
                    time.sleep(min(2 ** attempt, 8))
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            status = e.response.status_code if e.response is not None else None
            if status is not None and status not in (429, 500, 502, 503, 504):
                raise
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 8))
    if last_exc:
        raise last_exc
    raise requests.RequestException(
        f"GET {url} failed after {retries} retries (last status "
        f"{last_resp.status_code if last_resp is not None else 'unknown'})"
    )


def parse_dt(s: str | None) -> datetime | None:
    """解析时间为 UTC datetime。支持 ISO/RFC3339 与 RFC822 (mail-archive RSS 用)。"""
    if not s:
        return None
    # 1) ISO/RFC3339: "2026-07-12T04:27:40Z"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    # 2) RFC822: "Sun, 12 Jul 2026 07:42:05 GMT"
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_utc_iso(ts: str | None) -> str | None:
    """归一化 ISO 时间戳: 去毫秒, 统一为 YYYY-MM-DDTHH:MM:SSZ (字符串比较边界一致)。

    GitLab 返回 2026-07-12T10:30:00.000Z, mbox/lore 用 ...Z; 字符串比较时 '.'<'Z' 会错。
    """
    if not ts:
        return ts
    return re.sub(r"\.\d+(Z|[+-]\d{2}:?\d{2})$", r"\1", ts)


def classify_subject(subject: str) -> str:
    """根据邮件主题判断类型。"""
    if not subject:
        return "discussion"
    s = subject.strip()
    # [PATCH n/N], [PATCH v2], [RESEND ...], [Stable-x.y n/N] (回退补丁)
    if re.match(r"^\s*\[(patch|resend|stable)", s, re.IGNORECASE):
        return "patch"
    if re.match(r"^\s*\[(rfc|request for comment)", s, re.IGNORECASE):
        return "rfc"
    return "discussion"


_TAG_RE = re.compile(r"<[^>]+>")
# 邮件头 / patch trailer 行 (From:/Subject:/Signed-off-by: 等)
_HEADER_LINE_RE = re.compile(
    r"^(From|Subject|Date|To|Cc|Reply-To|Signed-off-by|Message-Id|"
    r"In-Reply-To|References):\s",
    re.IGNORECASE,
)


def strip_html(html: str) -> str:
    """粗略去 HTML 标签，保留文本与换行。"""
    if not html:
        return ""
    # 把 <pre> / <br> 转成换行
    text = re.sub(r"(?i)</?pre[^>]*>", "\n", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = _TAG_RE.sub("", text)
    # 反转义常见实体
    text = (text.replace("&lt;", "<").replace("&gt;", ">")
                .replace("&amp;", "&").replace("&quot;", '"')
                .replace("&#39;", "'"))
    return text


def extract_excerpt(body: str, max_chars: int = 600) -> str:
    """从邮件/描述正文取摘要：去引文、去邮件头/trailer、去 patch diff、截断。"""
    if not body:
        return ""
    lines: list[str] = []
    for ln in body.splitlines():
        if ln.startswith(">"):
            continue
        if _HEADER_LINE_RE.match(ln):
            continue
        if ln.startswith("diff --git") or ln.startswith("---") or ln.startswith("+++"):
            break
        if re.match(r"^@@ ", ln):
            break
        lines.append(ln)
    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text


def lore_msgid_from_url(url: str) -> str | None:
    """从 lore 邮件 URL 提取 message-id (去尖括号)。

    https://lore.kernel.org/qemu-devel/20260712041539.108341-38-mjt@tls.msk.ru/
    -> 20260712041539.108341-38-mjt@tls.msk.ru
    """
    if not url:
        return None
    path = urlsplit(url).path
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    return unquote(parts[-1])
