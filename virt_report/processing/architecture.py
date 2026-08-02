"""从社区原始文本中识别处理器架构。"""
from __future__ import annotations

import re
from collections.abc import Iterable


_ARCH_PATTERNS = (
    ("x86", re.compile(
        r"(?<![a-z0-9])(?:x86(?:_64)?|i[3-6]86|amd64|target/i386|kvm[/_-]?x86|"
        r"vmx|svm|tdx|sev(?:-snp)?)(?![a-z0-9])", re.IGNORECASE)),
    ("Arm", re.compile(
        r"(?<![a-z0-9])(?:arm(?:32|64)?|aarch64|target/arm|kvm[/_-]?arm|"
        r"gicv?[234]?|sve2?|sme2?)(?![a-z0-9])", re.IGNORECASE)),
    ("RISC-V", re.compile(
        r"(?<![a-z0-9])(?:risc-v|riscv|target/riscv)(?![a-z0-9])", re.IGNORECASE)),
    ("s390x", re.compile(
        r"(?<![a-z0-9])(?:s390x?|target/s390x)(?![a-z0-9])", re.IGNORECASE)),
    ("PowerPC", re.compile(
        r"(?<![a-z0-9])(?:powerpc|ppc64le?|target/ppc)(?![a-z0-9])", re.IGNORECASE)),
    ("LoongArch", re.compile(
        r"(?<![a-z0-9])(?:loongarch64?|target/loongarch)(?![a-z0-9])", re.IGNORECASE)),
    ("Hexagon", re.compile(
        r"(?<![a-z0-9])(?:hexagon|target/hexagon)(?![a-z0-9])", re.IGNORECASE)),
)

FOCUS_ARCHITECTURES = frozenset({"x86", "Arm"})


def normalize_architectures(architectures: Iterable[str]) -> list[str]:
    """统一结构化架构标签，同时兼容旧报告中的 ARM 写法。"""
    normalized: list[str] = []
    for architecture in architectures:
        label = "Arm" if str(architecture).casefold() == "arm" else str(architecture)
        if label and label not in normalized:
            normalized.append(label)
    return normalized


def detect_architectures(parts: Iterable[str | None]) -> list[str]:
    """按固定优先级返回文本中有明确证据的架构名称。"""
    text = "\n".join(part for part in parts if part)
    return [name for name, pattern in _ARCH_PATTERNS if pattern.search(text)]


def focus_priority(architectures: Iterable[str]) -> int:
    """x86/Arm 返回 0，供升序排序时前置展示。"""
    return 0 if FOCUS_ARCHITECTURES.intersection(
        normalize_architectures(architectures)
    ) else 1
