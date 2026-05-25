# sub2api-usage

[![CI](https://github.com/kadaliao/sub2api-usage/actions/workflows/ci.yml/badge.svg)](https://github.com/kadaliao/sub2api-usage/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/sub2api-usage.svg)](https://pypi.org/project/sub2api-usage/)
[![Python](https://img.shields.io/pypi/pyversions/sub2api-usage.svg)](https://pypi.org/project/sub2api-usage/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[sub2api](https://github.com/Wei-Shaw/sub2api) 后台用量查询的命令行工具。终端里直接看请求数、Token、成本、明细，支持今天 / 7 天 / 30 天 / 全部切换。

## 安装

```bash
# 推荐：uv tool install (隔离环境)
uv tool install sub2api-usage

# 或者用 pipx
pipx install sub2api-usage

# 或者直接 pip
pip install sub2api-usage
```

## 用法

```bash
# 首次运行会引导填写账号、密码、后台地址、时区，
# 配置保存到 ~/.config/sub2api-usage/config.json (chmod 600)
sub2api-usage

# 重新配置账号
sub2api-usage setup

# 非交互打印 (脚本 / 管道用)
sub2api-usage print
sub2api-usage print --period week --list --page-size 20
sub2api-usage print --period month --json
```

### 交互面板键位

| 键 | 功能 |
| --- | --- |
| `1` / `2` / `3` / `4` | 今天 / 7 天 / 30 天 / 全部 |
| `←` / `→` | 在标签间切换 |
| `n` / `p` | 下一页 / 上一页 |
| `r` | 刷新 |
| `q` | 退出 |

### 单位

数量统一用计算机领域的 K/M/G/T，耗时按 ms/s/min/h/d 自动选择最适合的尺度。

## License

[MIT](LICENSE)
