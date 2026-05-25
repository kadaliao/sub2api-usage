# ruff: noqa: E501
"""sub2api 用量查询工具

首次运行会引导填写账号信息并保存到 ~/.config/sub2api-usage/config.json (chmod 600)。
默认进入全屏交互式面板，可在今天 / 7 天 / 30 天 / 全部之间切换并翻页查看明细。

用法:
    sub2api-usage                # 进入交互面板
    sub2api-usage setup          # 重新配置账号
    sub2api-usage print          # 非交互打印 (脚本/管道用)
    sub2api-usage print --json
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import stat
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = "https://cc.aihezu.dev"
DEFAULT_TIMEZONE = "Asia/Shanghai"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "sub2api-usage"
CONFIG_FILE = CONFIG_DIR / "config.json"


# ===== Config =================================================================

def load_config() -> Optional[dict[str, str]]:
    if not CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_config(cfg: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    CONFIG_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)


# ===== API client =============================================================

class APIError(RuntimeError):
    pass


class Client:
    def __init__(self, base_url: str, email: str, password: str, timezone: str):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.timezone = timezone
        self._client = httpx.AsyncClient(timeout=30)
        self._token: Optional[str] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def login(self) -> None:
        try:
            r = await self._client.post(
                f"{self.base_url}/api/v1/auth/login",
                json={"email": self.email, "password": self.password},
            )
        except httpx.HTTPError as e:
            raise APIError(f"网络错误: {e}") from e
        if r.status_code != 200:
            raise APIError(f"登录失败 HTTP {r.status_code}: {r.text[:200]}")
        payload = r.json()
        if payload.get("code") != 0:
            raise APIError(f"登录失败: {payload.get('message')}")
        data = payload.get("data") or {}
        if data.get("requires_2fa"):
            raise APIError("该账号开启了二次验证 (TOTP)，本工具暂不支持。")
        token = data.get("access_token")
        if not token:
            raise APIError("登录响应未包含 access_token")
        self._token = token

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self._token:
            await self.login()
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            r = await self._client.get(f"{self.base_url}{path}", params=params, headers=headers)
            if r.status_code == 401:
                await self.login()
                headers = {"Authorization": f"Bearer {self._token}"}
                r = await self._client.get(f"{self.base_url}{path}", params=params, headers=headers)
        except httpx.HTTPError as e:
            raise APIError(f"网络错误: {e}") from e
        if r.status_code != 200:
            raise APIError(f"{path} HTTP {r.status_code}: {r.text[:200]}")
        payload = r.json()
        if payload.get("code") != 0:
            raise APIError(f"{path}: {payload.get('message')}")
        return payload.get("data") or {}

    async def stats(self, start: str, end: str) -> dict[str, Any]:
        return await self._get(
            "/api/v1/usage/stats",
            {"start_date": start, "end_date": end, "timezone": self.timezone},
        )

    async def list(self, start: str, end: str, page: int, page_size: int) -> dict[str, Any]:
        return await self._get(
            "/api/v1/usage",
            {
                "start_date": start,
                "end_date": end,
                "timezone": self.timezone,
                "page": page,
                "page_size": page_size,
                "sort_by": "created_at",
                "sort_order": "desc",
            },
        )


# ===== Humanize ===============================================================

def humanize_count(n: Any, decimals: int = 2) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if n == 0:
        return "0"
    sign = "-" if n < 0 else ""
    n = abs(n)
    for unit, scale in (("T", 1e12), ("G", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= scale:
            return f"{sign}{n / scale:.{decimals}f}{unit}"
    return f"{sign}{int(n)}" if n == int(n) else f"{sign}{n:.{decimals}f}"


def humanize_duration_ms(ms: Any) -> str:
    try:
        ms = float(ms)
    except (TypeError, ValueError):
        return str(ms)
    if ms < 1000:
        return f"{ms:.0f}ms"
    s = ms / 1000
    if s < 60:
        return f"{s:.2f}s"
    m = s / 60
    if m < 60:
        return f"{m:.2f}min"
    h = m / 60
    if h < 24:
        return f"{h:.2f}h"
    return f"{h / 24:.2f}d"


def humanize_money(v: Any) -> str:
    try:
        return f"${float(v):.4f}"
    except (TypeError, ValueError):
        return str(v)


# ===== Period helpers =========================================================

PERIODS = (
    ("today", "今天"),
    ("week", "7 天"),
    ("month", "30 天"),
    ("all", "全部"),
)


def period_range(period: str) -> tuple[str, str]:
    today = date.today()
    if period == "today":
        return today.isoformat(), today.isoformat()
    if period == "week":
        return (today - timedelta(days=6)).isoformat(), today.isoformat()
    if period == "month":
        return (today - timedelta(days=29)).isoformat(), today.isoformat()
    if period == "all":
        return "2000-01-01", today.isoformat()
    raise ValueError(f"unknown period: {period}")


# ===== Setup wizard ===========================================================

def _prompt(label: str, default: Optional[str] = None, secret: bool = False) -> str:
    text = label + (f" [{default}]" if default else "") + ": "
    while True:
        val = (getpass.getpass(text) if secret else input(text)).strip()
        if val:
            return val
        if default is not None:
            return default
        print("  请输入非空值")


async def run_setup(existing: Optional[dict[str, str]] = None) -> dict[str, str]:
    print()
    if existing is None:
        print("== sub2api-usage 首次配置 ==")
        print("(密码以明文保存到 ~/.config/sub2api-usage/config.json，文件权限 600)")
    else:
        print("== sub2api-usage 修改配置 ==")
        print(f"当前账号: {existing.get('email')}    地址: {existing.get('base_url')}")
    print()

    base_url = _prompt("后台地址", default=(existing or {}).get("base_url") or DEFAULT_BASE_URL)
    email = _prompt("邮箱", default=(existing or {}).get("email"))
    if existing and existing.get("password"):
        pwd = getpass.getpass("密码 (回车保留原值): ").strip() or existing["password"]
    else:
        pwd = _prompt("密码", secret=True)
    tz = _prompt("时区", default=(existing or {}).get("timezone") or DEFAULT_TIMEZONE)

    cfg = {"base_url": base_url, "email": email, "password": pwd, "timezone": tz}

    print("\n登录验证中...")
    client = Client(base_url, email, pwd, tz)
    try:
        await client.login()
    except APIError as e:
        print(f"[失败] {e}", file=sys.stderr)
        if input("是否重新输入？[Y/n] ").strip().lower() in ("", "y", "yes"):
            await client.aclose()
            return await run_setup(cfg)
        await client.aclose()
        raise SystemExit(1)
    await client.aclose()
    save_config(cfg)
    print(f"[OK] 已保存到 {CONFIG_FILE}\n")
    return cfg


# ===== Non-interactive print mode ============================================

async def cmd_print(cfg: dict[str, str], period: str, show_list: bool, page: int, page_size: int, as_json: bool) -> None:
    start, end = period_range(period)
    client = Client(cfg["base_url"], cfg["email"], cfg["password"], cfg["timezone"])
    try:
        stats = await client.stats(start, end)
        list_data = await client.list(start, end, page, page_size) if show_list else None
    finally:
        await client.aclose()

    if as_json:
        out: dict[str, Any] = {"range": {"start": start, "end": end, "timezone": cfg["timezone"]}, "stats": stats}
        if list_data is not None:
            out["list"] = list_data
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    _print_stats(stats, start, end, cfg["timezone"])
    if list_data is not None:
        _print_list(list_data)


def _print_stats(stats: dict[str, Any], start: str, end: str, tz: str) -> None:
    print(f"\n== 用量统计 [{start} ~ {end}] ({tz}) ==")
    fields = [
        ("total_requests", "请求数", humanize_count),
        ("total_tokens", "Token", humanize_count),
        ("total_input_tokens", "  输入", humanize_count),
        ("total_output_tokens", "  输出", humanize_count),
        ("total_cache_tokens", "  Cache", humanize_count),
        ("total_cost", "成本", humanize_money),
        ("total_actual_cost", "实际成本", humanize_money),
        ("average_duration_ms", "平均耗时", humanize_duration_ms),
    ]
    for key, label, fmt in fields:
        if stats.get(key) is not None:
            print(f"  {label:<12} {fmt(stats[key])}")


def _print_list(data: dict[str, Any]) -> None:
    items = data.get("items") or []
    print(f"\n== 明细 (第 {data.get('page')}/{data.get('pages')} 页, 共 {data.get('total')} 条) ==")
    header = f"{'time':<19}  {'model':<18} {'key':<14} {'group':<12} {'in':>7} {'out':>7} {'cache_r':>9} {'cost':>10} {'dur':>8}"
    print(f"  {header}")
    print(f"  {'-' * len(header)}")
    for row in items:
        print(
            f"  {(row.get('created_at') or '')[:19].replace('T', ' '):<19}  "
            f"{(row.get('model') or '')[:18]:<18} "
            f"{((row.get('api_key') or {}).get('name') or '')[:14]:<14} "
            f"{((row.get('group') or {}).get('name') or '')[:12]:<12} "
            f"{humanize_count(row.get('input_tokens') or 0):>7} "
            f"{humanize_count(row.get('output_tokens') or 0):>7} "
            f"{humanize_count(row.get('cache_read_tokens') or 0):>9} "
            f"{humanize_money(row.get('total_cost') or 0):>10} "
            f"{humanize_duration_ms(row.get('duration_ms') or 0):>8}"
        )


# ===== Interactive TUI ========================================================

def run_tui(cfg: dict[str, str]) -> None:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import DataTable, Footer, Header, Static, Tab, Tabs

    class UsageApp(App):
        CSS = """
        Screen { background: $surface; }
        #stats {
            padding: 1 2;
            margin: 0 1;
            border: round $primary;
            color: $text;
            height: auto;
        }
        Tabs { margin: 0 1; }
        DataTable { margin: 0 1; height: 1fr; }
        #status {
            dock: bottom;
            height: 1;
            background: $boost;
            color: $text-muted;
            padding: 0 2;
        }
        """
        BINDINGS = [
            Binding("q", "quit", "退出"),
            Binding("r", "refresh", "刷新"),
            Binding("n", "next_page", "下一页"),
            Binding("p", "prev_page", "上一页"),
            Binding("1", "set_period('today')", "今天"),
            Binding("2", "set_period('week')", "7 天"),
            Binding("3", "set_period('month')", "30 天"),
            Binding("4", "set_period('all')", "全部"),
        ]

        period = "today"
        page = 1
        page_size = 50

        def __init__(self, cfg: dict[str, str]):
            super().__init__()
            self.cfg = cfg
            self.client = Client(cfg["base_url"], cfg["email"], cfg["password"], cfg["timezone"])

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield Tabs(
                Tab("今天 (1)", id="today"),
                Tab("7 天 (2)", id="week"),
                Tab("30 天 (3)", id="month"),
                Tab("全部 (4)", id="all"),
            )
            yield Static("加载中...", id="stats")
            table: DataTable = DataTable(id="table", zebra_stripes=True, cursor_type="row")
            table.add_columns("时间", "模型", "Key", "Group", "输入", "输出", "Cache", "成本", "耗时")
            yield table
            yield Static(f"账号 {self.cfg['email']}  ·  {self.cfg['base_url']}", id="status")
            yield Footer()

        async def on_mount(self) -> None:
            self.title = "sub2api 用量"
            self.sub_title = self.cfg["email"]
            await self._refresh_data()

        async def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:  # noqa: F821
            new = event.tab.id if event.tab else None
            if new and new != self.period:
                self.period = new
                self.page = 1
                await self._refresh_data()

        async def action_set_period(self, p: str) -> None:
            self.query_one(Tabs).active = p

        async def action_refresh(self) -> None:
            await self._refresh_data()

        async def action_next_page(self) -> None:
            self.page += 1
            await self._refresh_data()

        async def action_prev_page(self) -> None:
            if self.page > 1:
                self.page -= 1
                await self._refresh_data()

        async def _refresh_data(self) -> None:
            start, end = period_range(self.period)
            stats_widget = self.query_one("#stats", Static)
            table = self.query_one(DataTable)
            stats_widget.update("加载中...")
            try:
                stats = await self.client.stats(start, end)
                list_data = await self.client.list(start, end, self.page, self.page_size)
            except APIError as e:
                stats_widget.update(f"[red]错误: {e}[/]")
                return
            stats_widget.update(self._render_stats(stats, start, end))
            table.clear()
            for row in list_data.get("items", []):
                table.add_row(
                    (row.get("created_at") or "")[:19].replace("T", " "),
                    (row.get("model") or "")[:24],
                    ((row.get("api_key") or {}).get("name") or "")[:16],
                    ((row.get("group") or {}).get("name") or "")[:14],
                    humanize_count(row.get("input_tokens") or 0),
                    humanize_count(row.get("output_tokens") or 0),
                    humanize_count(row.get("cache_read_tokens") or 0),
                    humanize_money(row.get("total_cost") or 0),
                    humanize_duration_ms(row.get("duration_ms") or 0),
                )
            total = list_data.get("total", 0)
            pages = list_data.get("pages", 1)
            page = list_data.get("page", self.page)
            self.query_one("#status", Static).update(
                f"账号 {self.cfg['email']}  ·  范围 {start} ~ {end}  ·  第 {page}/{pages} 页 · 共 {total} 条 "
                f"(n 下一页 · p 上一页 · r 刷新 · q 退出)"
            )

        @staticmethod
        def _render_stats(stats: dict[str, Any], start: str, end: str) -> str:
            req = humanize_count(stats.get("total_requests") or 0)
            tok = humanize_count(stats.get("total_tokens") or 0)
            tin = humanize_count(stats.get("total_input_tokens") or 0)
            tout = humanize_count(stats.get("total_output_tokens") or 0)
            tcache = humanize_count(stats.get("total_cache_tokens") or 0)
            cost = humanize_money(stats.get("total_cost") or 0)
            actual = humanize_money(stats.get("total_actual_cost") or 0)
            dur = humanize_duration_ms(stats.get("average_duration_ms") or 0)
            return (
                f"[b]{start} ~ {end}[/]\n"
                f"请求 [cyan]{req}[/]    Token [cyan]{tok}[/]   ([dim]in[/] {tin} · [dim]out[/] {tout} · [dim]cache[/] {tcache})\n"
                f"成本 [yellow]{cost}[/]   实际 [yellow]{actual}[/]   平均耗时 [magenta]{dur}[/]"
            )

        async def on_unmount(self) -> None:
            await self.client.aclose()

    UsageApp(cfg).run()


# ===== CLI ====================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="sub2api 用量查询")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("setup", help="(重新) 配置账号信息")

    pp = sub.add_parser("print", help="非交互打印 (脚本/管道用)")
    pp.add_argument("--period", default="today", choices=[k for k, _ in PERIODS])
    pp.add_argument("--list", action="store_true", help="同时拉取明细")
    pp.add_argument("--page", type=int, default=1)
    pp.add_argument("--page-size", type=int, default=20)
    pp.add_argument("--json", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    cfg = load_config()

    if args.cmd == "setup":
        asyncio.run(run_setup(cfg))
        return 0

    if cfg is None:
        print("未检测到配置，进入引导...")
        cfg = asyncio.run(run_setup(None))

    if args.cmd == "print":
        try:
            asyncio.run(cmd_print(cfg, args.period, args.list, args.page, args.page_size, args.json))
        except APIError as e:
            print(f"[错误] {e}", file=sys.stderr)
            return 1
        return 0

    # default: TUI
    run_tui(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
