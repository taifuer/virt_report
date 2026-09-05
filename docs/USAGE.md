# 使用指南

以下命令均在项目根目录执行，并使用项目虚拟环境。

## 安装与配置

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
```

在 `.env` 中填写 `DEEPSEEK_API_KEY`。`GITLAB_TOKEN` 为可选项；生产环境还应设置随机长字符串 `METRICS_ACCESS_KEY`。数据源、模型、站点地址和调度时间由 `config.yaml` 管理。

## 采集与检查

```bash
# 采集近 3 天并重建线程
.venv/bin/virt-report fetch --since-days 3

# 从指定本地日期回填；max-pages 控制分页来源的上限
.venv/bin/virt-report fetch --since 2026-04-01 --max-pages 20

# 查看来源覆盖范围和最近采集状态
.venv/bin/virt-report status

# 回填较早的 KVM public-inbox epoch
.venv/bin/virt-report backfill-kvm --since 2025-01-01
```

较长回填建议分段执行并在每段后检查 `status`。qemu-devel 首次触及新月份时可能下载完整 mbox，KVM 历史回填也可能产生较大网络流量。

## 生成报告

```bash
# 默认生成前一个完整自然日、自然周或自然月
.venv/bin/virt-report daily
.venv/bin/virt-report weekly
.venv/bin/virt-report monthly

# 生成指定周期
.venv/bin/virt-report daily 2026-07-12
.venv/bin/virt-report weekly 2026-W28
.venv/bin/virt-report monthly 2026-07

# 复用数据库，不在生成前采集
.venv/bin/virt-report daily 2026-07-12 --no-fetch

# AI 失败时不发布正式报告，适合自动任务
.venv/bin/virt-report daily --no-fetch --require-ai
```

不带 `--require-ai` 的手工命令可生成模板内容用于排查。自动调度始终要求有效 AI 结果。

## Web、专题与静态快照

```bash
# 启动 SQLite 驱动的动态站点
.venv/bin/virt-report serve --host 127.0.0.1 --port 8090

# 重建离线专题快照和已点评内容搜索索引
.venv/bin/virt-report topics-refresh
.venv/bin/virt-report search-refresh

# 导出完整静态站点到 site/
.venv/bin/virt-report index

# 预览 Git 中的静态快照
.venv/bin/python -m http.server 8091 --directory site
```

动态服务直接读取 SQLite，不要求每次报告生成后全量导出。只有执行 `index`，或设置 `schedule.auto_export: true`，才会刷新完整 `site/` 快照。

## 会议内容维护

```bash
# 更新 KVM Forum 标题并生成年度主题分析
.venv/bin/virt-report kvm-forum

# 复用已保存的 KVM Forum 标题
.venv/bin/virt-report kvm-forum --no-fetch

# 拉取学术会议目录并列出待人工复核候选
.venv/bin/virt-report conference-catalog \
  --from-year 2010 --to-year 2026 --list-candidates

# DBLP 限流时按会议续跑
.venv/bin/virt-report conference-catalog \
  --venue vee --from-year 2010 --to-year 2026

# 补充公开摘要与已核验单位，并同步公开快照
.venv/bin/virt-report conference-catalog --no-fetch \
  --enrich-abstracts --abstract-limit 20
.venv/bin/virt-report conference-catalog --no-fetch \
  --enrich-affiliations --sync-public-metadata

# 缺少 DOI 时执行严格标题匹配；此操作较慢
.venv/bin/virt-report conference-catalog --no-fetch \
  --discover-dois --enrich-affiliations --sync-public-metadata
```

学术会议条目需要人工复核后才能进入公开内容；维护过程不读取论文 PDF。

## 版本时间线

```bash
# 核对官方发布记录，仅更新版本快照与 versions.html
.venv/bin/virt-report versions-refresh --from-year 2003
# 维护者审核后更新仓库内公开基线，不重新联网
.venv/bin/virt-report versions-refresh --no-fetch --update-bundled
```

从关于页顶部“查看版本”进入版本时间线，主导航不增加“版本”项。
默认精简视图按年展示 QEMU 的功能版本，不只保留整数大版本；可切换 KVM、Libvirt 或全部项目，并按年份筛选。
各年份默认展开，精简视图可点击年份收起或展开；切换筛选或视图时重新展开，版本直达链接也会展开对应年份。
详细视图提供发布要点、来源链接及官方提供的提交数、贡献者数，支持
10／20／30 条分页，数量按功能版本计。两种视图均不列出维护补丁及 RC，
页面底部提供官方完整版本记录入口。KVM 按 Linux 版本标注；旧编号按各项目历史规则识别。
已采集的维护版本仍保留在离线数据中，不因页面精简而删除。

报告归档、论文、专题、搜索与版本详细视图使用同一分页组件。
改变筛选或每页数量回到第一页，翻页滚动到列表开头；URL 保留筛选和分页参数。

采集结果位于 `data/versions.json`，小型 HTTP 缓存位于 `data/versions/http/`。
`virt_report/content/versions.json` 提供可直接预览的公开基线；
`version_notes.json` 保存根据官方材料人工整理的中文要点。后续尚未整理的要点
保留官方原文或发布链接，不调用 AI 推断特性是否落入某个版本。
网页只读快照；来源失败保留原记录，独立刷新不会重建全站。

## 备份与恢复

```bash
# 创建一致性的 gzip 快照并输出 SHA-256
.venv/bin/virt-report backup data/backups/virt-report.db.gz

# 创建默认路径快照，并清理 7 天前的 auto-*.db.gz
.venv/bin/virt-report backup --keep-days 7

# 停止 Web 和调度器后恢复；原数据库会先自动备份
.venv/bin/virt-report restore data/backups/virt-report.db.gz \
  --sha256 <摘要> --force
```

不要直接复制正在写入的 SQLite 文件。生产部署的完整操作见[部署与运维](DEPLOYMENT.md)。

## 开发检查

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pyflakes virt_report tests
```
