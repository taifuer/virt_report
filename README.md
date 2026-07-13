# virt-report

追踪 **libvirt / QEMU / KVM** 社区动态（邮件列表 + GitLab issue/MR），用 LLM 生成**中文**日报 / 周报 / 月报，以简洁的网页形式呈现。

## 界面预览

| 首页 | 日报 | 关于 |
|---|---|---|
| [![virt-report 首页演示](docs/images/demo-home.png)](docs/images/demo-home.png) | [![virt-report 日报演示](docs/images/demo-daily.png)](docs/images/demo-daily.png) | [![virt-report 关于页演示](docs/images/demo-about.png)](docs/images/demo-about.png) |

## 数据源（均已验证可用）

| 源 | 地址 | 采集方式 | 历史覆盖 |
|---|---|---|---|
| qemu-devel 邮件列表 | `lists.gnu.org/archive/mbox/qemu-devel/` | **整月 mbox**（~52MB，含 Message-ID/In-Reply-To） | ✅ 完整历史 |
| kvm 邮件列表 | `lore.kernel.org/kvm/git/1.git` | public-inbox Git epoch（按时间浅克隆） | ✅ 当前 epoch；支持历史回填 |
| libvirt 邮件列表 | `lists.libvirt.org` | 官方月归档 + HyperKitty REST API | ✅ 按月回填 |
| libvirt GitLab | `gitlab.com/libvirt/libvirt` | GitLab API v4 (issues / MRs) | ✅ |
| qemu-project GitLab | `gitlab.com/qemu-project/qemu` | GitLab API v4 (issues；MRs 被 QEMU 禁用) | ✅ |

> QEMU 不接受 GitLab MR，所有补丁走 qemu-devel 邮件列表，故 qemu-project 的 MR 端点返回 403（已优雅跳过）。
> **qemu-devel** 用 GNU pipermail 整月 mbox，本地缓存 `data/mbox/`，带 **If-Modified-Since**（未改 304 跳过 52MB 重下）+ 批量去重。
> **KVM** 不再轮询 `/new.atom` 的 25 条窗口，而是同步 lore/public-inbox Git epoch，保留原始 RFC822 邮件头。日常只按时间浅克隆；`backfill-kvm` 可回填 epoch 0/1。
> **libvirt** 使用官方 HyperKitty：月度页面发现 thread hash，REST API 获取真实 Message-ID、父邮件、作者、正文和线程关系；不再依赖第三方 mail-archive RSS。
> 数据库以 `(source, project, native_id)` 唯一标识条目，并使用 `activity_at` 统一邮件发信时间与 GitLab 最近活动时间。
> 时间解析：`base.parse_dt` 同时支持 ISO 与 RFC822（mail-archive RSS 用 RFC822，曾因只认 ISO 导致 kvm/libvirt 日期全误标今天）。
> DeepSeek V4 使用 `thinking={type:enabled}` + `reasoning_effort=high`；JSON 输出通过不可变 `Txxx` 证据引用回填真实 URL。max_tokens 日 12000 / 周 24000 / 月 32000，并记录实际 usage。

## 架构

```
采集层 (lore/gitlab) ──> SQLite ──> 处理层 (线程折叠/分类/排序) ──> 总结层 (DeepSeek) ──> 渲染层 (Jinja2) ──> site/
```

- **增量 + 幂等**：采集器记录游标，只拉新数据；重跑从 DB 重新生成。
- **线程为中心**：邮件按 `in-reply-to` 链折叠成线程（一个 50 封的 patch series = 1 线程），不按单封邮件。
- **LLM 前置压缩**：先按项目分别取配额（QEMU 50%、Libvirt 25%、KVM 25%），再按显著性排序；上下文包含首封、最新回复、评审信号与 GitLab 状态。
- **可观测采集**：`fetch_runs` 记录每个源的请求窗口、覆盖范围、完整性和错误；单源失败不阻断其他源，失败不推进水位。
- **降级容错**：未配置 DeepSeek key 时自动走模板降级，仍产出报告。

## 安装

```bash
cd virt_report
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env   # 填入 DEEPSEEK_API_KEY (可选, 不填则降级)
```

## 使用

```bash
# 采集近 3 天数据 + 重建线程
.venv/bin/virt-report fetch

# 从指定本地日期开始回填（适合补齐历史月报数据）
.venv/bin/virt-report fetch --since 2026-04-01 --max-pages 20

# 检查数据覆盖、最近采集完整性和错误
.venv/bin/virt-report status

# 回填 KVM 历史（跨度大时会下载较大的 Git epoch）
.venv/bin/virt-report backfill-kvm --since 2025-01-01

# 生成今天的日报 (全链路: 采集 -> 处理 -> DeepSeek 总结 -> 渲染)
.venv/bin/virt-report daily

# 生成指定日期的日报
.venv/bin/virt-report daily 2026-07-12

# 周报 / 月报 (默认本周/本月；可传 2026-W28 / 2026-07)
.venv/bin/virt-report weekly
.venv/bin/virt-report monthly 2026-07

# 仅用已采集数据重新生成 (不重新采集)
.venv/bin/virt-report daily 2026-07-12 --no-fetch

# 启动数据库驱动的站点（报告按路由即时渲染）
.venv/bin/virt-report serve --host 0.0.0.0 --port 8090

# 导出完整静态快照到 site/（首页为 site/index.html）
.venv/bin/virt-report index

# 本地预览 Git 中的纯静态站点
.venv/bin/python -m http.server 8091 --directory site
```

日常运行以 SQLite 中的报告为准，Web 服务通过 `/daily/<date>.html`、`/weekly/<W>.html` 和 `/monthly/<YYYY-MM>.html` 路由即时渲染，不再为每次生成重复写 HTML。执行 `virt-report index` 时会把首页、关于页及全部报告导出到仓库的 `site/` 目录；该目录作为可直接部署的静态快照纳入 Git。

首页默认展示最近 14 期日报及最近 6 期周报/月报；更早内容通过右侧日、周、月切换器查找。报告条目按原始证据标注“功能 / 缺陷 / 其他”，并对 x86、ARM 架构议题进行明确标记和优先展示。

## 启用 AI 摘要（DeepSeek）

在 `.env` 中设置 `DEEPSEEK_API_KEY`（获取：https://platform.deepseek.com/）。DeepSeek 走 OpenAI 兼容端点 `https://api.deepseek.com`，日报用 `deepseek-v4-flash`（快速低成本）、周/月报用 `deepseek-v4-pro`（高质量，见 `config.yaml`）。`.env` 由 `load_config()` 自动加载，无需手动 export。无 key 时自动降级为模板摘要并标注「降级模板」徽章。

> 日报使用 `deepseek-v4-flash`，周/月报使用 `deepseek-v4-pro`。模型支持 1M 上下文和 JSON Output；项目仍限制候选线程数以降低噪声与成本。若模型 ID 变化，调整 `config.yaml` 即可。

## 定时调度

```cron
# 频繁采集（KVM Git 增量、libvirt 官方 API、QEMU mbox/GitLab）
7 */4 * * *   /path/to/virt_report/scripts/run_fetch.sh
# 日报 (基于累积数据生成前一天)
17 9 * * *    /path/to/virt_report/scripts/run_daily.sh
# 周报 (周一生成上周) / 月报 (1 号生成上月)
23 9 * * 1    /path/to/virt_report/scripts/run_weekly.sh
33 9 1 * *    /path/to/virt_report/scripts/run_monthly.sh
```

## 项目结构

```
virt_report/
├── config.yaml                 # 数据源/LLM/调度配置
├── virt_report/
│   ├── config.py  db.py  cli.py
│   ├── collectors/{base,lore,gitlab}.py
│   ├── processing/{threads,classify,rank,architecture,category}.py
│   ├── summarize/{llm_provider,prompts,periods,report}.py
│   ├── render/{render.py, templates/{base,index,report,about}.html}
│   └── server.py              # SQLite 驱动的动态报告路由
├── scripts/run_daily.sh
├── data/virt_report.db         # SQLite (gitignore)
└── site/                       # 纳入 Git 的可部署静态站点快照
```

## 路线图

- ✅ **阶段 1 (MVP)**：lore (qemu-devel/kvm) + GitLab (libvirt/qemu) 采集 → SQLite → 线程折叠/排序 → DeepSeek 日报 → 报纸风 HTML。端到端跑通。
- ✅ **阶段 2**：HyperKitty 采集器补 libvirt-devel (RSS+thread hash 折叠 patch series)；通用周期报告生成器；周报；日历导航首页。
- ✅ **阶段 3**：月报；周/月报主题聚类 (LLM 跨源归纳 themes)；cron 全调度 (频繁采集 + 日/周/月报)。
- ⏳ **阶段 4**：RSS/邮件订阅、站内搜索、patch series 折叠细化、扩展数据源。
