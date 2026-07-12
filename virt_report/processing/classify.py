"""分类：从主题提取子系统标签、确定线程类型。"""
from __future__ import annotations

import re

# [PATCH v3 5/9] hw/audio/virtio-sound: ...  -> hw/audio
# [Stable-10.0.12 38/75] hw/nvme: ...        -> hw/nvme
# target/arm: foo                              -> target/arm
_BRACKET_SUBSYS = re.compile(r"^[^\]]*\]\s*([a-zA-Z0-9_./-]+):")
_BARE_SUBSYS = re.compile(r"^([a-zA-Z0-9_./-]+):")
_LEADER_RE = re.compile(r"^(re|fwd|aw|resend):\s*", re.IGNORECASE)


def _strip_leader(subject: str) -> str:
    s = subject.strip()
    while True:
        new = _LEADER_RE.sub("", s)
        if new == s:
            break
        s = new
    return s.strip()


def extract_topic(subject: str | None) -> str | None:
    """从邮件/issue 主题提取子系统标签 (如 hw/nvme, target/arm)。"""
    if not subject:
        return None
    s = _strip_leader(subject)
    m = _BRACKET_SUBSYS.match(s) or _BARE_SUBSYS.match(s)
    if not m:
        return None
    topic = m.group(1).strip(".")
    parts = [p for p in topic.split("/") if p]
    # 只保留前两级，避免过长
    return "/".join(parts[:2]) if parts else None


def thread_kind(kinds: list[str]) -> str:
    """根据线程内各条目类型确定线程整体类型 (优先级高者胜出)。"""
    kset = set(kinds or [])
    for k in ("rfc", "patch", "security", "bug", "mr", "issue", "discussion"):
        if k in kset:
            return k
    return "discussion"


def best_subject(subjects: list[str]) -> str | None:
    """从一组主题里选最适合展示的：优先非 Re: 的根主题。"""
    if not subjects:
        return None
    cleaned = [s for s in subjects if s]
    if not cleaned:
        return None
    # 优先不含 Re:/Fwd: 的
    non_re = [s for s in cleaned if not _LEADER_RE.match(s)]
    # 优先 cover letter [PATCH 0/N]
    cover = [s for s in (non_re or cleaned) if re.search(r"\[patch[^\]]*\b0/\d+", s, re.IGNORECASE)]
    if cover:
        return cover[0]
    if non_re:
        return non_re[0]
    return cleaned[0]
