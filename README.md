# sub2api-usage

[![CI](https://github.com/kadaliao/sub2api-usage/actions/workflows/ci.yml/badge.svg)](https://github.com/kadaliao/sub2api-usage/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/sub2api-usage.svg)](https://pypi.org/project/sub2api-usage/)
[![Python](https://img.shields.io/pypi/pyversions/sub2api-usage.svg)](https://pypi.org/project/sub2api-usage/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[sub2api](https://github.com/Wei-Shaw/sub2api) 后台用量查询的命令行工具。普通模式聚焦统计数据，一屏展示今天、昨天、7 天、30 天的请求数、Token、成本和平均耗时；管理员模式提供账户、用户、订阅等运维视图。

## 安装

```bash
# 推荐：uv tool install (隔离环境)
uv tool install sub2api-usage

# 或者用 pipx
pipx install sub2api-usage
```

## 用法

```bash
# 首次运行会引导填写账号、密码、后台地址、时区，
# 配置保存到 ~/.config/sub2api-usage/config.json (chmod 600)
# 默认打开统计面板：今天 / 昨天 / 7 天 / 30 天
sub2api-usage

# 配置账号：没有 profile 时直接进入新增；已有 profile 时先选择要修改的 profile 或新增
sub2api-usage setup

# 明确新增普通 profile
sub2api-usage profiles add <name>
sub2api-usage profiles list
sub2api-usage profiles use <name>
sub2api-usage profiles remove <name>

# 非交互打印 (脚本 / 管道用)
# 默认一屏打印今天 / 昨天 / 7 天 / 30 天
sub2api-usage print

# 脚本场景也可以只打印单个时段
sub2api-usage print --period week
sub2api-usage print --period month --json
```

### 交互面板键位

| 键 | 功能 |
| --- | --- |
| `r` | 刷新 |
| `q` | 退出 |

普通面板不展示请求明细，只展示统计性质的数据：今天、昨天、最近 7 天、最近 30 天。

### 单位

数量统一用计算机领域的 K/M/G/T，耗时按 ms/s/min/h/d 自动选择最适合的尺度。

## 管理员模式

如果你的账号在 sub2api 后端有 `role=admin`，可以用 `admin` 子命令查看上游账户用量窗口/配额、平台用户消费排行、用户订阅（每日/每周/每月用量与到期时间）等。管理员凭据保存在 `~/.config/sub2api-usage/config.json` 的 `admin_profiles` 命名空间，与普通账号互不影响。

```bash
# 首次运行会引导填写管理员邮箱/密码，登录后会校验 role=admin
sub2api-usage admin

# 配置管理员账号：没有 admin profile 时直接进入新增；已有时先选择要修改的 profile 或新增
sub2api-usage admin setup

# 明确新增管理员 profile
sub2api-usage admin profiles add <name>

# 非交互打印
sub2api-usage admin print --view dashboard
sub2api-usage admin print --view accounts --json
sub2api-usage admin print --view users
sub2api-usage admin print --view users --sort-by week
sub2api-usage admin print --view subscriptions --json
sub2api-usage admin print --view subscriptions --status all --sort-by monthly_usage

# 管理 admin profile
sub2api-usage admin profiles list
sub2api-usage admin profiles use <name>
sub2api-usage admin profiles remove <name>
```

### 管理员面板键位

通用：

| 键 | 功能 |
| --- | --- |
| `d` / `a` / `u` / `s` | 切到 Dashboard / 上游账户 / 用户排行 / 订阅管理 |
| `r` | 刷新 |
| `q` | 退出 |

Dashboard / Accounts 视图：

| 键 | 功能 |
| --- | --- |
| `1` / `2` / `3` / `4` / `5` | 切换时段：今天 / 昨天 / 7 天 / 30 天 / 全部 |

Users 视图（多时段并排展示，按当前列倒序）：

| 键 | 功能 |
| --- | --- |
| `1` / `2` / `3` / `4` / `5` | 按今天 / 昨天 / 7 天 / 30 天 / 全部 倒序 |
| `e` | 按邮箱升序 |
| `o` | 排序键循环 |
| `/` | 搜索当前表格，底部状态栏以 `/关键字` 输入并高亮匹配数据；`Enter` 完成，`Esc` 清除 |

Subscriptions 视图：

| 键 | 功能 |
| --- | --- |
| `1` / `3` / `4` | 按日 / 周 / 月用量倒序 |
| `e` / `i` / `x` | 按邮箱 / ID / 到期升序 |
| `S` | 状态过滤循环：active → expired → paused → all |
| `o` | 排序键循环 |
| `/` | 搜索当前表格，底部状态栏以 `/关键字` 输入并高亮匹配数据；`Enter` 完成，`Esc` 清除 |

数据拉取时当前视图会显示加载指示器，避免误以为卡死。

### 视图

- **Dashboard**：今日 / 累计请求与成本、用户数、上游账户分类计数 (正常/异常/限流/过载)、RPM/TPM、平均耗时、运行时长，以及当前时段模型 Top。
- **Accounts**：所有上游账户列表，含 5h 窗口 (`current_window_cost` / `window_cost_limit`) 与 7d 窗口用量、窗口截止时间、今日请求 / Token / 成本、当前并发、活跃会话/RPM、最后使用时间。
- **Users**：用户消费排行，**今天 / 昨天 / 7 天 / 30 天 / 全部** 五列实际成本并排展示，默认按今日倒序。当前排序列在表头标注 `↓` 或 `↑`，方括号里是该列的快捷键。
- **Subscriptions**：管理员订阅列表，默认只显示 `active`，可用 `S` 切换状态、`o` 或快捷键切换排序。含用户、分组、状态、日/周/月用量与对应分组限额、到期时间与剩余天数。

## License

[MIT](LICENSE)
