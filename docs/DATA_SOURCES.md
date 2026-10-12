# 数据源与采集

virt-report 只聚合公开可访问的社区数据。所有时间在写入数据库时统一为 UTC，报告切窗再按 `config.yaml` 中的站点时区处理，默认使用 `Asia/Shanghai`。

## 数据源

| 来源 | 采集方式 | 主要内容 |
|---|---|---|
| [qemu-devel](https://lists.gnu.org/archive/mbox/qemu-devel/) | GNU 月度 mbox + HTTP Range 增量 | QEMU 补丁、RFC 与评审讨论 |
| [KVM](https://lore.kernel.org/kvm/) | lore/public-inbox Git epoch | KVM 内核补丁与邮件线程 |
| [libvirt-devel](https://lists.libvirt.org/archives/list/devel@lists.libvirt.org/) | 官方 HyperKitty 归档与 REST API | libvirt 补丁和社区讨论 |
| [QEMU GitLab](https://gitlab.com/qemu-project/qemu) | GitLab API v4 | Issue；项目不接受 GitLab MR |
| [libvirt GitLab](https://gitlab.com/libvirt/libvirt) | GitLab API v4 | Issue 与 Merge Request |

具体地址和启用状态由 `config.yaml` 管理。公开 GitLab 项目无需令牌；设置 `GITLAB_TOKEN` 可提高 API 限额。

## 增量策略

### qemu-devel

GNU 归档按月提供 mbox。采集器将文件缓存到 `data/mbox/`，再次采集时用 64 KiB 尾部重叠验证后追加新增字节，验证失败才重新下载完整月份。时间窗口会在解析后过滤邮件，但首次涉及某个月份时仍需取得对应 mbox；跨境网络较慢的服务器宜提前预热或复制缓存。

### KVM

采集器同步 `https://lore.kernel.org/kvm/git/1.git`，读取原始 RFC822 邮件头和正文，不受 Atom 最近 25 条窗口限制。日常采集按时间浅克隆；较早历史通过 `backfill-kvm` 从 epoch 0/1 回填。跨度较大时 Git 数据量会明显增加。

可选的 lore Atom 采集器每次从最新页重新扫描，避免续扫旧页时漏掉新邮件。只有实际到达请求起点才标记完整；近期 feed 没有下一页，并不代表历史窗口已采集齐全。

### libvirt-devel

采集器先从 HyperKitty 月度归档发现线程，再通过官方 REST API 获取 Message-ID、父邮件、作者、正文和线程关系。官方数据写入成功后会移除旧镜像记录，避免同一邮件重复出现。

### GitLab

GitLab 活动使用 `updated_after` 一类的精确时间窗口，并保留创建与最近活动时间。QEMU 的 MR 端点返回 403 属于项目策略，采集器会跳过而不将其视为整轮失败。

新采集到的 GitLab 活动会保存独立快照。回看旧周期时，只使用截至期末已记录的对应状态，不拿当前标题、正文或状态替代历史。升级前已被覆盖的内容无法恢复：旧活动时间可以用于统计，但缺少可信历史正文的记录不进入 AI 候选。完成一次 API 扫描不等于恢复了此前未保存的全部历史。

## 版本发布记录

版本时间线独立于邮件与 Issue/MR 采集，默认每天核对一次：

- QEMU：官方发布博客、GitLab 发布标签与早期 qemu-devel 发布公告。
  公告日期优先；轻量标签缺少日期时只用提交月份定位公告，不用提交时间代替。
  官方提供时保留提交数与贡献者数。
- Libvirt：官方 `news.html` 与 GitLab 发布标签，跳过 unreleased。
  2.0 之前三段编号是功能版本，四段编号才是维护版本；之后按 x.y.0／x.y.z 区分。
- KVM：从首次包含 KVM 的 Linux 2.6.20 起记录；2.6.x 与后续 x.y 是功能版本。
  官方 stable 标签索引用于发现版本，日期读取 `kernel.googlesource.com` 上的
  annotated release tag，不使用压缩包修改时间。离线数据保留各分支最新维护版，
  页面只展示功能版本，不将内核的全部维护更新表述为 KVM 更新。
  服务器无法连接 Google 镜像时，改用 kernel.org 官方发布清单与
  Torvalds 官方 GitHub 仓库的 annotated tags，沿用已核验历史日期和要点，
  只读取缺失功能版本的标签日期；不克隆内核仓库，不使用提交时间代替发布日期。
- KVM 要点引用维护者的合并标签或主线合并提交说明；缺少可用说明时只提供
  已核验的发布日期与原始链接。合并说明不代表后续修复的完整清单。

历史基线已追溯到 QEMU 2003 年、Libvirt 2005 年和 KVM 2007 年。
维护版本数据保留用于核验，不在精简或详细视图中逐条展示；完整记录链接到官方来源。
早期无法核验发布日期的记录暂不展示，不保证覆盖每一个历史补丁版本。
带日期标签同时保留 UTC 时间戳；公告使用发布者记录的日期，GitLab 标签使用 API 日期。
重签名日期与提交日期相隔超过七天的标签不直接用于确定发布日期。
来源页面增量校验并缓存，版本以 `(project, version)` 去重；访问页面不联网。
同一历史归档并发读取只下载一次；确认无公告的历史查询缓存 30 天，网络失败不记作无数据。
代码行数暂不统计，避免混淆整个 Linux 内核与 KVM 子系统的规模。

## 一致性与可观测性

- 条目以 `(source, project, native_id)` 唯一标识，重复采集不会重复写入。
- `activity_at` 统一表示邮件发信时间或 GitLab 最近活动时间。
- 时间解析同时支持 ISO/RFC 3339 与 RFC 822，避免邮件日期被误标为采集当天。
- `fetch_runs` 保留采集结果、数据日期范围和错误；单源失败不阻断其他来源。
- `collection_batches` / `collection_scans` 记录批次、实际请求窗口与扫描结果。仅完整扫描的窗口可以证明覆盖；成功扫描后没有新条目也是有效结果。数据库最早、最晚条目的日期不能证明中间无缺口，最近四天的成功采集也不能证明历史月报完整。
- 报告覆盖分为完整、部分完整和未知，并保留来源与缺口。旧数据没有扫描凭据时标记未知，不反推完整，也不会因此永久阻止历史报告生成。
- 同批次重试只处理失败或未完整来源；翻页预算逐步增加，达到上限仍明确标记未完整。尚未完成的请求起点保留到后续采集批次，不随滚动时间窗口丢弃。
- 邮件依据 Message-ID 与 In-Reply-To 重建线程；缺少父子关系时再使用规范化主题辅助折叠。
- 报告按本期实际活动选择线程，排序与证据同时受周期边界限制，后续回复不会使旧周期线程消失，也不会提前进入旧报告。

运行数据库、mbox 和 public-inbox 缓存均位于 `data/`，不得提交到 Git。对外使用数据时请同时遵守[数据政策](DATA_POLICY.md)与原始社区的许可和引用要求。
