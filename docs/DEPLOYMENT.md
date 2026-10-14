# 部署与运维

推荐在单台 Linux 服务器上使用 Docker Compose 部署。服务器需要访问 GitHub、GitLab、邮件列表、lore.kernel.org 和 DeepSeek API，并由 Nginx 或 Caddy 提供公网域名与 HTTPS。

## 准备配置

```bash
git clone https://github.com/taifuer/virt_report.git
cd virt_report
cp .env.example .env
```

编辑 `.env`：

- `DEEPSEEK_API_KEY`：自动生成 AI 报告必需。
- `METRICS_ACCESS_KEY`：生产环境必需，应使用随机长字符串。
- `GITLAB_TOKEN`：可选，用于提高 GitLab API 限额。
- `PYTHON_IMAGE`、`DEBIAN_MIRROR`、`PIP_INDEX_URL`：默认适合中国大陆网络，海外环境可覆盖。

密钥、数据库和采集缓存不得提交到 Git。

## 首次启动

源码仓库不携带 `data/`。已有实例推荐将快照复制到 `data/backups/` 后恢复；全新实例可以冷启动，但首次 qemu-devel mbox 与 KVM Git 下载可能耗时较长。

```bash
# 先构建并启动 Web
docker compose up -d --build web

# 方案 A：恢复已有快照（推荐）
docker compose stop web scheduler
docker compose run --rm web virt-report --config /app/config.yaml restore \
  /app/data/backups/virt-report.db.gz --sha256 <摘要> --force

# 方案 B：空库冷启动
docker compose run --rm scheduler virt-report --config /app/config.yaml \
  fetch --since-days 4
docker compose run --rm scheduler virt-report --config /app/config.yaml \
  daily --no-fetch

# 启动全部服务
docker compose up -d web scheduler
docker compose ps
docker compose logs -f scheduler
```

`web` 与 `scheduler` 共享宿主机 `data/` 和 `site/`，并使用 `restart: unless-stopped`。Web 只绑定 `127.0.0.1:8090`，不应直接暴露到公网。

## 默认调度

| 任务 | 时间 | 行为 |
|---|---|---|
| 增量采集 | 每 4 小时的第 7 分钟 | 更新数据并重建线程/离线索引 |
| 日报 | 每天 00:15 | 生成前一个完整自然日 |
| 周报 | 周一 00:25 | 生成上一个完整自然周 |
| 月报 | 每月 1 日 00:35 | 生成上一个完整自然月 |
| 自动备份 | 每天 01:05 | 创建一致性快照，只保留最新一份 |
| 版本记录 | 每天 03:35 | 检查正式版本与维护更新；默认只更新运行数据，启用 auto_export 才导出版本页 |

周期报告固定使用 `--no-fetch --require-ai`，不会与定时采集重复下载，也不会发布降级内容。单个任务默认超时 1 小时，含首次在内最多执行 3 次，重试间隔 15 分钟。状态持久化在 SQLite，容器重启后可继续；进程锁会阻止重复容器或重叠 cron 并行写库。

采集命令退出码为 `0`（成功）、`3`（部分成功）、`1`（失败）。调度器会有限重试后两种结果；同批次已完整的来源不重复下载，未完成来源继续保留原请求起点。进入新的定时批次后，仍会补此前未完成的窗口，不因“最近四天”持续向前滚动而跳过缺口。

报告生成前会检查目标周期的可证覆盖。必要邮件来源存在缺口时，前两次先等待数据补齐（退出码 `4`），不调用 AI；最后一次不再无限等待，可基于已有证据生成并显示数据不完整说明。GitLab 等补充来源缺口同样保留说明，但不单独阻塞发布。历史窗口没有扫描凭据时标记未知，不作为永久拦截条件。已正常发布的报告不会被自动反复生成，后续采集补齐不会触发重复付费点评；需要更新时再手工生成。

调度器首次启动只检查当前分钟。已有调度状态时，重启会补查最近 24 小时；先前异常中断的运行会记录为 `interrupted`。动态 Web 直接读取数据库，默认不在每次任务后重建全站静态文件。

## 反向代理与健康检查

- `/healthz`：检查 Web 与数据库是否可访问。
- `/readyz`：公开 `ok/degraded`；来源超过 12 小时未完整采集或应生成的报告未发布时返回 503。
- `/metrics.html`、`/api/metrics`、`/api/status`：使用 `METRICS_ACCESS_KEY` 保护，展示来源状态、数据规模、调度记录和模型用量。

浏览器验证后的安全 Cookie 默认有效 12 小时。运行指标页不写入静态快照，避免绕过鉴权。反向代理应保留应用的 CSP、防嵌入、MIME 嗅探和 Referrer Policy 等响应头，并配置 HTTPS。

Web 启动时初始化数据库，页面请求使用只读连接，不重复执行迁移检查。模板编译结果在进程内复用；报告列表只读取摘要字段，上下篇导航只查询相邻正稿。报告与生成状态不做延迟缓存，后台提交后下一次访问即可读取。SQLite 使用 WAL，采集写入期间仍可读取已提交数据。恢复数据库时仍需按下文停止服务后操作。

## 更新部署

```bash
git pull --ff-only
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 scheduler
```

若服务器有部署定制，更新前先保存差异，并将可配置项迁移到 `.env`。不要将线上临时修改、密钥或 `data/` 提交回仓库。

## 备份与恢复

调度器每天生成 `data/backups/auto-YYYY-MM-DD.db.gz`，默认 `schedule.backup_keep_count: 1`。
新快照通过数据库完整性检查并成功写入后，才清理较旧的自动备份；失败时保留原有备份。
自动清理不处理手工备份。只保留一份适合节省空间，但无法回溯更早的数据状态。
手工创建快照：

```bash
docker compose exec scheduler virt-report --config /app/config.yaml \
  backup /app/data/backups/virt-report.db.gz
```

恢复前停止所有可能写入数据库的服务，并核对命令输出的 SHA-256：

```bash
docker compose stop web scheduler
docker compose run --rm web virt-report --config /app/config.yaml restore \
  /app/data/backups/virt-report.db.gz --sha256 <摘要> --force
docker compose up -d web scheduler
```

不要用普通文件复制替代 SQLite 一致性备份。

## 宿主机 cron（可选）

不使用容器调度器时，可调用 `scripts/` 下的包装脚本：

```cron
7 */4 * * * /path/to/virt_report/scripts/run_fetch.sh
15 0 * * *  /path/to/virt_report/scripts/run_daily.sh
25 0 * * 1  /path/to/virt_report/scripts/run_weekly.sh
35 0 1 * *  /path/to/virt_report/scripts/run_monthly.sh
```

不要同时启用容器调度器和宿主机 cron。
