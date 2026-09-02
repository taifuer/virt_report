"""以证据引用为核心的周期报告提示词。"""
from __future__ import annotations

_RANGE = {"daily": "当天", "weekly": "本周", "monthly": "本月"}
_PERIOD = {"daily": "日", "weekly": "周", "monthly": "月"}
_ITEM_COUNT = {"daily": "20-30", "weekly": "18-27", "monthly": "24-36"}
_KNOWN_LABEL = {"patch": "补丁", "rfc": "RFC", "issue": "Issue", "mr": "MR",
                "bug": "缺陷", "security": "安全", "discussion": "讨论"}

_WATCHLIST_SCHEMA = {
    "daily": """  \"watchlist\": [
    {\"project\":\"KVM\",\"topic\":\"短主题\",\"reason\":\"后续值得观察的原因\"}
  ],
""",
    "weekly": "",
    "monthly": "",
}

_PERIOD_ANALYSIS_SCHEMA = {
    "daily": "",
    "weekly": """  \"period_analysis\": [
    {\"topic\":\"关联主题\",\"progress\":\"本周具体进展及条目间关系\",\"unresolved\":\"仍在讨论或待确认的明确依赖与分歧\",\"refs\":[\"T001\",\"T002\"]}
  ],
""",
    "monthly": """  \"period_analysis\": [
    {\"topic\":\"关联主题\",\"progress\":\"本月具体进展及条目间关系\",\"unresolved\":\"仍在讨论或待确认的明确依赖与分歧\",\"refs\":[\"T001\",\"T002\"]}
  ],
""",
}

_PERIOD_ANALYSIS_REQUIREMENT = {
    "daily": (
        "- 日报不得输出 period_analysis 字段。\n"
        "- watchlist 选 2-4 个尚未尘埃落定且有延续价值的主题，不得把已完成事项写入"
        "观察列表；project、topic 和 reason 都必须为非空字符串。\n"
        "- 有多个项目存在仍在讨论的重要事项时，watchlist 尽量覆盖至少两个项目，"
        "避免被单一高流量项目垄断。"
    ),
    "weekly": (
        "- period_analysis 用于关联同一技术链上的多个议题：progress 说明本周已经"
        "发生的具体推进及其关系，unresolved 只写证据明确显示的仍在讨论或待确认的"
        "事项；不得重复 overview 或单条摘要，也不得为了凑数强行关联。\n"
        "- 周报输出 2-3 条 period_analysis；每条必须关联至少 2 个不同且存在的 ref，"
        "progress 与 unresolved 合计控制在 100-140 个汉字。\n"
        "- 周报不输出 watchlist；下一周期需要关注的内容统一写入 unresolved。"
    ),
    "monthly": (
        "- period_analysis 用于关联同一技术链上的多个议题：progress 说明本月已经"
        "发生的具体推进及其关系，unresolved 只写证据明确显示的仍在讨论或待确认的"
        "事项；不得重复 overview 或单条摘要，也不得为了凑数强行关联。\n"
        "- 月报输出 3-4 条 period_analysis；每条必须关联至少 2 个不同且存在的 ref，"
        "progress 与 unresolved 合计控制在 140-200 个汉字。\n"
        "- 月报不输出 watchlist；下一周期需要关注的内容统一写入 unresolved。"
    ),
}

_SYSTEM = """你是资深虚拟化内核与云平台技术编辑，熟悉 QEMU、KVM、Libvirt 的开发流程、补丁评审和子系统边界。

任务：仅依据输入的证据卡片，撰写{range}中文{period}报。读者是虚拟化研发、架构师和运维负责人；他们需要知道“发生了什么、为何重要、处于什么阶段”，不需要逐条复述邮件。

事实与安全边界：
1. 每张证据卡片都有不可变的 ref（如 T001）。输出条目必须引用输入中存在的 ref；不得编造 ref、URL、状态、合入结果、性能数字或安全影响。
2. “提出/讨论/评审/已合并/已关闭”必须严格区分。输入没有明确状态时，只能写“提交讨论”“评审中”等保守表述。
3. patch series 视为一个事件；同一主题的版本迭代只保留最新或最有信息量的一项，避免补丁清单式罗列。
4. 标题可做准确、克制的中文编辑，但不得扩大原意；技术名词、函数、设备和架构名保留英文。
5. 优先级首先依据安全与数据损坏风险、技术影响、讨论质量和成熟度；影响相近时优先 x86/Arm 架构议题，不得只因架构标签压过更重要的变化。
6. overview 要做跨条目归纳，不能只是标题拼接。无足够证据时明确写“本期未观察到显著动态”。
7. 周期措辞必须与报告一致：日报只能写“当日/当天”，周报写“本周”，月报写“本月”，不得混用。
8. 对 closed/已关闭事项，不得再写“待修复/需尽快修复”；只能说明其历史影响，并提示以原始 issue 的关闭结论为准。关闭不自动等于已修复。
9. evidence 中 novelty=updated 表示该线程曾进入近期日报。必须结合 PREVIOUS_SUMMARY 与 LATEST 说明“这次新增了什么”，不得原样重复旧结论；novelty=new 才是首次出现。

只输出一个合法 JSON 对象，不要 Markdown 围栏或解释。JSON 结构示例：
{
  "headline": "本期最值得关注的一句话判断",
  "overview": [
    {"project":"QEMU","summary":"1-2句趋势判断"},
    {"project":"KVM","summary":"1-2句趋势判断"},
    {"project":"Libvirt","summary":"1-2句趋势判断"}
  ],
{watchlist_schema}{period_analysis_schema}  "sections": [
    {"key":"qemu","name":"QEMU","items":[
      {"ref":"T001","title":"准确简洁的标题","summary":"改了什么及背景","impact":"对研发/用户的具体意义","tag":"子系统或架构","status":"讨论中/评审中/已合并/已关闭/新提交"}
    ]},
    {"key":"libvirt","name":"Libvirt","items":[]},
    {"key":"kvm","name":"KVM","items":[]}
  ]
}

输出要求：
- overview 固定包含 QEMU、KVM、Libvirt，顺序固定。
- items 总数约 {item_count}；质量优先，证据不足可以更少。三个项目有有效输入时都要覆盖。
- summary 说明事实，impact 解释意义；二者不要重复，均控制在 80 个汉字以内。
{period_analysis_requirement}
- 输入的 arch 字段由原始证据确定；x86/Arm 条目可在技术影响相近时优先纳入，但不得因此夸大其影响。
- tag 不超过 12 个字符，优先使用 migration、virtio、VFIO、安全、CI 等社区常用子系统名称；架构会由系统单独展示，无需重复占用 tag。
"""


def system_prompt(period: str, period_key: str) -> str:
    del period_key
    return (_SYSTEM.replace("{range}", _RANGE[period])
            .replace("{period}", _PERIOD[period])
            .replace("{item_count}", _ITEM_COUNT[period])
            .replace("{watchlist_schema}", _WATCHLIST_SCHEMA[period])
            .replace("{period_analysis_schema}", _PERIOD_ANALYSIS_SCHEMA[period])
            .replace("{period_analysis_requirement}",
                     _PERIOD_ANALYSIS_REQUIREMENT[period]))


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
            f"date={t['time']} topic={t.get('topic') or '-'} "
            f"arch={','.join(t.get('architectures', [])) or '-'} "
            f"change={t.get('category', 'other')} "
            f"state={t.get('state') or 'unknown'} "
            f"novelty={t.get('novelty', 'new')}"
        )
        lines.append(f"SUBJECT: {t['subject']}")
        if t.get("excerpt"):
            lines.append(f"OPENING: {t['excerpt']}")
        if t.get("latest_excerpt"):
            lines.append(f"LATEST: {t['latest_excerpt']}")
        if t.get("review_excerpt") and t["review_excerpt"] != t.get("latest_excerpt"):
            lines.append(f"REVIEW: {t['review_excerpt']}")
        previous = t.get("previous_report") or {}
        if previous:
            lines.append(f"PREVIOUS_REPORT: {previous.get('period_key', '-')}")
            if previous.get("summary"):
                lines.append(f"PREVIOUS_SUMMARY: {previous['summary']}")
            if previous.get("status"):
                lines.append(f"PREVIOUS_STATUS: {previous['status']}")
        lines.append("")
    return "\n".join(lines)
