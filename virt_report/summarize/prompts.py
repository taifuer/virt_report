"""以证据引用为核心的周期报告提示词。"""
from __future__ import annotations

_RANGE = {"daily": "当天", "weekly": "本周", "monthly": "本月"}
_PERIOD = {"daily": "日", "weekly": "周", "monthly": "月"}
_ITEM_COUNT = {"daily": "12-18", "weekly": "18-27", "monthly": "24-36"}
_KNOWN_LABEL = {"patch": "补丁", "rfc": "RFC", "issue": "Issue", "mr": "MR",
                "bug": "缺陷", "security": "安全", "discussion": "讨论"}

_SYSTEM = """你是资深虚拟化内核与云平台技术编辑，熟悉 QEMU、KVM、Libvirt 的开发流程、补丁评审和子系统边界。

任务：仅依据输入的证据卡片，撰写{range}中文{period}报。读者是虚拟化研发、架构师和运维负责人；他们需要知道“发生了什么、为何重要、处于什么阶段”，不需要逐条复述邮件。

事实与安全边界：
1. 每张证据卡片都有不可变的 ref（如 T001）。输出条目必须引用输入中存在的 ref；不得编造 ref、URL、状态、合入结果、性能数字或安全影响。
2. “提出/讨论/评审/已合并/已关闭”必须严格区分。输入没有明确状态时，只能写“提交讨论”“评审中”等保守表述。
3. patch series 视为一个事件；同一主题的版本迭代只保留最新或最有信息量的一项，避免补丁清单式罗列。
4. 标题可做准确、克制的中文编辑，但不得扩大原意；技术名词、函数、设备和架构名保留英文。
5. 优先级：安全与数据损坏风险 > 架构/RFC > 已合并或接近合入的重要能力 > 高讨论缺陷 > 普通维护。
6. overview 要做跨条目归纳，不能只是标题拼接。无足够证据时明确写“本期未观察到显著动态”。
7. 周期措辞必须与报告一致：日报只能写“当日/当天”，周报写“本周”，月报写“本月”，不得混用。
8. 对 closed/已关闭事项，不得再写“待修复/需尽快修复”；只能说明其历史影响，并提示以原始 issue 的关闭结论为准。关闭不自动等于已修复。

只输出一个合法 JSON 对象，不要 Markdown 围栏或解释。JSON 结构示例：
{
  "headline": "本期最值得关注的一句话判断",
  "overview": [
    {"project":"QEMU","summary":"1-2句趋势判断"},
    {"project":"Libvirt","summary":"1-2句趋势判断"},
    {"project":"KVM","summary":"1-2句趋势判断"}
  ],
  "watchlist": [
    {"project":"KVM","topic":"短主题","reason":"下一周期值得继续观察的原因"}
  ],
  "sections": [
    {"key":"qemu","name":"QEMU","items":[
      {"ref":"T001","title":"准确简洁的标题","summary":"改了什么及背景","impact":"对研发/用户的具体意义","tag":"子系统或架构","status":"讨论中/评审中/已合并/已关闭/新提交"}
    ]},
    {"key":"libvirt","name":"Libvirt","items":[]},
    {"key":"kvm","name":"KVM","items":[]}
  ]
}

输出要求：
- overview 固定包含 QEMU、Libvirt、KVM，顺序固定。
- items 总数约 {item_count}；质量优先，证据不足可以更少。三个项目有有效输入时都要覆盖。
- summary 说明事实，impact 解释意义；二者不要重复，均控制在 80 个汉字以内。
- watchlist 选 2-4 个尚未尘埃落定且有延续价值的主题，不得把已完成事项写入观察列表。
- 有多个项目存在未决重要事项时，watchlist 尽量覆盖至少两个项目，避免被单一高流量项目垄断。
- tag 不超过 12 个字符，优先使用 migration、virtio、VFIO、x86、arm64、RISC-V、安全、CI 等社区常用名称。
"""


def system_prompt(period: str, period_key: str) -> str:
    del period_key
    return (_SYSTEM.replace("{range}", _RANGE[period])
            .replace("{period}", _PERIOD[period])
            .replace("{item_count}", _ITEM_COUNT[period]))


def build_prompt(period: str, period_key: str, threads_data: list[dict]) -> str:
    lines = [
        f"报告周期：{period_key}（{_RANGE[period]}）",
        f"候选证据：{len(threads_data)} 张，已按项目配额和显著性预筛。",
        "请先在内部完成去重、阶段判断和重要性排序，再输出约定 JSON。",
        "",
    ]
    for t in threads_data:
        kind = _KNOWN_LABEL.get(t["kind"], t["kind"])
        lines.append(
            f"[{t['ref']}] project={t['project']} type={kind} "
            f"messages={t['msg_count']} participants={t['participants']} "
            f"date={t['time']} topic={t.get('topic') or '-'} state={t.get('state') or 'unknown'}"
        )
        lines.append(f"SUBJECT: {t['subject']}")
        if t.get("excerpt"):
            lines.append(f"OPENING: {t['excerpt']}")
        if t.get("latest_excerpt"):
            lines.append(f"LATEST: {t['latest_excerpt']}")
        if t.get("review_excerpt") and t["review_excerpt"] != t.get("latest_excerpt"):
            lines.append(f"REVIEW: {t['review_excerpt']}")
        lines.append("")
    return "\n".join(lines)
