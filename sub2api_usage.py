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
import re
import stat
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = "https://cc.aihezu.dev"
DEFAULT_TIMEZONE = "Asia/Shanghai"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "sub2api-usage"
CONFIG_FILE = CONFIG_DIR / "config.json"


# ===== Config =================================================================

def load_config() -> Optional[dict[str, Any]]:
    if not CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if "profiles" not in data and "email" in data:
        return {"default": "default", "profiles": {"default": data}}
    if isinstance(data.get("profiles"), dict):
        return data
    return None


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    CONFIG_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)


def resolve_profile(
    cfg: dict[str, Any],
    name: Optional[str] = None,
    namespace: str = "profiles",
) -> tuple[str, dict[str, str]]:
    default_key = "admin_default" if namespace == "admin_profiles" else "default"
    profiles = cfg.get(namespace) or {}
    target = name or cfg.get(default_key)
    if not target:
        hint = "sub2api-usage admin setup" if namespace == "admin_profiles" else "sub2api-usage setup"
        raise APIError(f"配置中没有可用 profile，请先运行 '{hint}'")
    if target not in profiles:
        avail = ", ".join(profiles) or "(空)"
        raise APIError(f"profile '{target}' 不存在；现有: {avail}")
    return target, profiles[target]


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
        self.role: Optional[str] = None

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
        self.role = (data.get("user") or {}).get("role")

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

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        if not self._token:
            await self.login()
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            r = await self._client.post(f"{self.base_url}{path}", json=body, headers=headers)
            if r.status_code == 401:
                await self.login()
                headers = {"Authorization": f"Bearer {self._token}"}
                r = await self._client.post(f"{self.base_url}{path}", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise APIError(f"网络错误: {e}") from e
        if r.status_code != 200:
            raise APIError(f"{path} HTTP {r.status_code}: {r.text[:200]}")
        payload = r.json()
        if payload.get("code") != 0:
            raise APIError(f"{path}: {payload.get('message')}")
        return payload.get("data") or {}

    # ----- Admin endpoints (require role=admin) -----

    async def admin_dashboard_stats(self) -> dict[str, Any]:
        return await self._get("/api/v1/admin/dashboard/stats", {})

    async def admin_dashboard_snapshot(
        self,
        start: str,
        end: str,
        granularity: str = "day",
        include_models: bool = True,
        models_limit: int = 5,
    ) -> dict[str, Any]:
        return await self._get(
            "/api/v1/admin/dashboard/snapshot-v2",
            {
                "start_date": start,
                "end_date": end,
                "timezone": self.timezone,
                "granularity": granularity,
                "include_stats": "true",
                "include_trend": "false",
                "include_model_stats": "true" if include_models else "false",
                "include_group_stats": "false",
            },
        )

    async def admin_users_ranking(self, start: str, end: str, limit: int = 50) -> dict[str, Any]:
        return await self._get(
            "/api/v1/admin/dashboard/users-ranking",
            {"start_date": start, "end_date": end, "timezone": self.timezone, "limit": limit},
        )

    async def admin_accounts(self, page: int = 1, page_size: int = 200) -> dict[str, Any]:
        return await self._get(
            "/api/v1/admin/accounts",
            {"page": page, "page_size": page_size, "sort_by": "priority", "sort_order": "desc"},
        )

    async def admin_accounts_today_batch(self, account_ids: list[int]) -> dict[str, Any]:
        if not account_ids:
            return {}
        return await self._post(
            "/api/v1/admin/accounts/today-stats/batch",
            {"account_ids": account_ids},
        )

    async def admin_account_usage(
        self, account_id: int, source: Optional[str] = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if source:
            params["source"] = source
        return await self._get(f"/api/v1/admin/accounts/{account_id}/usage", params)

    async def admin_account_usage_batch(
        self,
        accounts: list[dict[str, Any]],
        concurrency: int = 8,
    ) -> dict[int, dict[str, Any]]:
        """按账户类型分别拉 UsageInfo。

        参考 web 端 AccountUsageCell.vue:
        - anthropic + (oauth/setup-token): source=passive
        - gemini / antigravity(oauth) / openai(oauth): 不传 source (后端默认 active)
        - 其余 (apikey, bedrock, service_account 等): 跳过, 用 account.quota_* 字段
        """
        if not accounts:
            return {}
        sem = asyncio.Semaphore(concurrency)

        targets: list[tuple[int, Optional[str]]] = []
        for acc in accounts:
            aid = acc.get("id")
            if aid is None:
                continue
            platform = (acc.get("platform") or "").lower()
            atype = (acc.get("type") or "").lower()
            if platform == "anthropic" and atype in ("oauth", "setup-token"):
                targets.append((aid, "passive"))
            elif platform == "gemini":
                targets.append((aid, None))
            elif platform == "antigravity" and atype == "oauth":
                targets.append((aid, None))
            elif platform == "openai" and atype == "oauth":
                targets.append((aid, None))

        async def one(aid: int, src: Optional[str]) -> tuple[int, Optional[dict[str, Any]]]:
            async with sem:
                try:
                    info = await self.admin_account_usage(aid, source=src)
                except APIError:
                    return aid, None
                return aid, info

        results = await asyncio.gather(*(one(aid, src) for aid, src in targets))
        return {aid: info for aid, info in results if info is not None}

    async def admin_subscriptions(
        self,
        page: int = 1,
        page_size: int = 200,
        user_id: Optional[int] = None,
        group_id: Optional[int] = None,
        status: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "sort_by": "expires_at",
            "sort_order": "asc",
        }
        if user_id:
            params["user_id"] = user_id
        if group_id:
            params["group_id"] = group_id
        if status:
            params["status"] = status
        if platform:
            params["platform"] = platform
        return await self._get("/api/v1/admin/subscriptions", params)


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
    ("yesterday", "昨天"),
    ("week", "7 天"),
    ("month", "30 天"),
    ("all", "全部"),
)
DEFAULT_PERIOD = "today"


def period_range(period: str) -> tuple[str, str]:
    today = date.today()
    if period == "today":
        return today.isoformat(), today.isoformat()
    if period == "yesterday":
        y = today - timedelta(days=1)
        return y.isoformat(), y.isoformat()
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


async def run_setup(
    cfg: Optional[dict[str, Any]] = None,
    name: Optional[str] = None,
    namespace: str = "profiles",
) -> dict[str, Any]:
    cfg = cfg or {}
    is_admin = namespace == "admin_profiles"
    default_key = "admin_default" if is_admin else "default"
    role_label = "管理员" if is_admin else "账号"
    setup_cmd = "sub2api-usage admin" if is_admin else "sub2api-usage"

    profiles: dict[str, dict[str, str]] = dict(cfg.get(namespace) or {})
    profile_name = name or cfg.get(default_key) or ("admin" if is_admin else "default")

    if not profiles:
        print(f"\n== {setup_cmd} 首次配置 ({role_label}) ==")
        print("(密码以明文保存到 ~/.config/sub2api-usage/config.json，文件权限 600)")
    elif profile_name in profiles:
        print(f"\n== 修改 {role_label} profile: {profile_name} ==")
    else:
        print(f"\n== 新建 {role_label} profile: {profile_name} ==")
        print(f"  现有 profile: {', '.join(profiles)}")
    print()

    existing = profiles.get(profile_name) or {}
    base_url = _prompt("后台地址", default=existing.get("base_url") or DEFAULT_BASE_URL)
    email = _prompt(f"{role_label}邮箱", default=existing.get("email"))
    if existing.get("password"):
        pwd = getpass.getpass("密码 (回车保留原值): ").strip() or existing["password"]
    else:
        pwd = _prompt("密码", secret=True)
    tz = _prompt("时区", default=existing.get("timezone") or DEFAULT_TIMEZONE)

    entry = {"base_url": base_url, "email": email, "password": pwd, "timezone": tz}

    print("\n登录验证中...")
    client = Client(base_url, email, pwd, tz)
    login_err: Optional[APIError] = None
    try:
        await client.login()
        if is_admin and (client.role or "").lower() != "admin":
            raise APIError(f"当前账号非管理员 (role={client.role!r})，无法用于 admin 模式")
    except APIError as e:
        login_err = e
    finally:
        await client.aclose()

    if login_err is not None:
        print(f"[失败] {login_err}", file=sys.stderr)
        if input("是否重新输入？[Y/n] ").strip().lower() in ("", "y", "yes"):
            profiles[profile_name] = entry
            merged = dict(cfg)
            merged[namespace] = profiles
            return await run_setup(merged, profile_name, namespace)
        raise SystemExit(1)

    profiles[profile_name] = entry
    new_cfg = dict(cfg)
    new_cfg[namespace] = profiles
    if not new_cfg.get(default_key):
        new_cfg[default_key] = profile_name
    save_config(new_cfg)
    print(f"[OK] {role_label} profile '{profile_name}' 已保存到 {CONFIG_FILE}")
    if len(profiles) > 1 and new_cfg[default_key] != profile_name:
        switch_cmd = f"{setup_cmd} profiles use {profile_name}"
        print(f"     当前 default 仍是 '{new_cfg[default_key]}' (用 '{switch_cmd}' 切换)")
    print()
    return new_cfg


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

        period = DEFAULT_PERIOD
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
                active=DEFAULT_PERIOD,
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
            table.loading = True
            try:
                try:
                    stats = await self.client.stats(start, end)
                    list_data = await self.client.list(start, end, self.page, self.page_size)
                except APIError as e:
                    stats_widget.update(f"[red]错误: {e}[/]")
                    return
            finally:
                table.loading = False
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


# ===== Admin print ============================================================

ADMIN_VIEWS = ("dashboard", "accounts", "users", "subscriptions")
ACCOUNT_WINDOW_COL_WIDTH = 42


def _last_used_short(ts: Any) -> str:
    s = str(ts or "")
    return s[:19].replace("T", " ") if s else "-"


def _parse_iso(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    s = str(ts).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _expires_short(ts: Any) -> str:
    dt = _parse_iso(ts)
    if dt is None:
        return "-"
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    delta = dt - now
    days = delta.days
    date = dt.strftime("%Y-%m-%d")
    if delta.total_seconds() < 0:
        return f"{date} (已过期 {-days}d)"
    if days >= 1:
        return f"{date} (剩 {days}d)"
    hours = int(delta.total_seconds() // 3600)
    return f"{date} (剩 {hours}h)"


def _humanize_seconds(s: Any) -> str:
    try:
        s = float(s)
    except (TypeError, ValueError):
        return str(s)
    if s < 60:
        return f"{s:.0f}s"
    m = s / 60
    if m < 60:
        return f"{m:.0f}min"
    h = m / 60
    if h < 24:
        return f"{h:.1f}h"
    return f"{h / 24:.1f}d"


def _format_used_limit(used: Optional[float], limit: Optional[float]) -> str:
    if used is None and limit is None:
        return "-"
    if not limit:
        return f"{humanize_money(used or 0)} / -"
    pct = (float(used or 0) / float(limit)) * 100
    return f"{humanize_money(used or 0)} / {humanize_money(limit)} ({pct:.0f}%)"


_WINDOW_DEADLINE_FIELDS = (
    "end_time",
    "end_at",
    "ends_at",
    "window_end",
    "window_end_at",
    "period_end",
    "period_end_at",
    "reset_at",
    "reset_time",
    "resets_at",
    "next_reset_at",
    "rate_limit_reset_at",
    "session_window_end",
)


def _window_deadline_short(ts: Any) -> str:
    dt = _parse_iso(ts)
    if dt is None:
        return ""
    return dt.strftime("%m-%d %H:%M")


def _pick_window_deadline(*sources: Optional[dict[str, Any]]) -> str:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for field in _WINDOW_DEADLINE_FIELDS:
            short = _window_deadline_short(source.get(field))
            if short:
                return short
    return ""


def _append_window_deadline(rendered: str, deadline: str) -> str:
    if not deadline or rendered == "-":
        return rendered
    return f"{rendered} · 至 {deadline}"


def _format_progress(progress: dict[str, Any], limit: Optional[float] = None) -> str:
    """格式化 UsageProgress (five_hour / seven_day) 为 '${cost} / ${limit} (util%)'。"""
    util = progress.get("utilization")
    stats = progress.get("window_stats") or {}
    cost = stats.get("cost") if stats else None
    if limit is None and stats:
        # 反推 limit
        if util and util > 0 and cost is not None:
            limit = float(cost) / (float(util) / 100)
    parts = []
    if cost is not None:
        parts.append(humanize_money(cost))
    if limit:
        parts.append(f"/ {humanize_money(limit)}")
    if util is not None:
        parts.append(f"({float(util):.0f}%)")
    rendered = " ".join(parts) if parts else "-"
    return _append_window_deadline(rendered, _pick_window_deadline(progress, stats))


def _account_5h_window(account: dict[str, Any], usage: Optional[dict[str, Any]] = None) -> str:
    """Anthropic OAuth/SetupToken 账号的 5 小时窗口。优先 UsageInfo.five_hour。"""
    if usage:
        five = usage.get("five_hour")
        if five:
            limit = account.get("window_cost_limit")
            return _format_progress(five, limit=limit)
    win = account.get("current_window_cost")
    lim = account.get("window_cost_limit")
    if win is None and lim is None:
        return "-"
    rendered = _format_used_limit(win, lim)
    return _append_window_deadline(rendered, _pick_window_deadline(account))


def _account_seven_day_window(account: dict[str, Any], usage: Optional[dict[str, Any]] = None) -> str:
    """账号 7d 窗口（Anthropic OAuth seven_day / seven_day_sonnet，或 Gemini daily 平替）。

    账户级别只有 5h 和 7d 两类滑动窗口。Gemini 没有 7d 概念，用各自的 daily 顶替显示。
    quota_* 字段是 key/订阅级配额，不属于账户窗口，这里不再回退。
    """
    if not usage:
        return "-"
    for key in ("seven_day", "seven_day_sonnet", "gemini_shared_daily", "gemini_pro_daily", "gemini_flash_daily"):
        progress = usage.get(key)
        if progress:
            return _format_progress(progress)
    return "-"


def _account_concurrency_display(account: dict[str, Any]) -> str:
    cur = account.get("current_concurrency") or 0
    limit = account.get("concurrency")
    if limit:
        return f"{cur}/{limit}"
    return str(cur)


def _account_sessions_rpm(account: dict[str, Any]) -> str:
    """额外的实时指标：活跃会话 / RPM。仅 Anthropic OAuth/SetupToken 启用相应限制时有值。"""
    parts: list[str] = []
    if account.get("active_sessions") is not None:
        max_s = account.get("max_sessions")
        parts.append(f"sess {account['active_sessions']}{'/' + str(max_s) if max_s else ''}")
    if account.get("current_rpm") is not None:
        base = account.get("base_rpm")
        parts.append(f"rpm {account['current_rpm']}{'/' + str(base) if base else ''}")
    return " · ".join(parts) if parts else "-"


async def cmd_admin_print(
    cfg: dict[str, str],
    view: str,
    period: str,
    as_json: bool,
    sub_status: str = "active",
    sub_sort_by: str = "daily_usage",
    user_sort_by: str = "today",
) -> None:
    start, end = period_range(period)
    client = Client(cfg["base_url"], cfg["email"], cfg["password"], cfg["timezone"])
    try:
        if view == "dashboard":
            stats = await client.admin_dashboard_stats()
            snapshot = await client.admin_dashboard_snapshot(start, end)
            if as_json:
                print(json.dumps({"stats": stats, "snapshot": snapshot}, ensure_ascii=False, indent=2))
            else:
                _print_admin_dashboard(stats, snapshot)
        elif view == "accounts":
            data = await client.admin_accounts()
            items = data.get("items") or []
            ids = [a["id"] for a in items if a.get("id") is not None]
            today = await client.admin_accounts_today_batch(ids) if ids else {}
            usage_map = await client.admin_account_usage_batch(items) if items else {}
            if as_json:
                print(json.dumps(
                    {"accounts": items, "today_stats": today, "usage": usage_map},
                    ensure_ascii=False, indent=2,
                ))
            else:
                _print_admin_accounts(items, today, usage_map)
        elif view == "users":
            items, totals = await _fetch_users_multi(client)
            items = _sort_users(items, user_sort_by)
            if as_json:
                print(json.dumps(
                    {"users": items, "totals": totals, "sort_by": user_sort_by},
                    ensure_ascii=False, indent=2,
                ))
            else:
                _print_admin_users(items, totals, sort_by=user_sort_by)
        elif view == "subscriptions":
            status_filter = None if sub_status == "all" else sub_status
            data = await client.admin_subscriptions(
                page=1, page_size=200, status=status_filter,
            )
            items = _sort_subscriptions(data.get("items") or [], sub_sort_by)
            data["items"] = items
            if as_json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                _print_admin_subscriptions(data, status=sub_status, sort_by=sub_sort_by)
        else:
            raise APIError(f"未知 view: {view}")
    finally:
        await client.aclose()


def _print_admin_dashboard(stats: dict[str, Any], snapshot: dict[str, Any]) -> None:
    print("\n== 管理员总览 ==")
    fields = [
        ("today_requests", "今日请求", humanize_count),
        ("today_input_tokens", "今日输入", humanize_count),
        ("today_output_tokens", "今日输出", humanize_count),
        ("today_cost", "今日成本", humanize_money),
        ("today_actual_cost", "今日实际", humanize_money),
        ("total_requests", "累计请求", humanize_count),
        ("total_tokens", "累计 Token", humanize_count),
        ("total_cost", "累计成本", humanize_money),
        ("total_actual_cost", "累计实际", humanize_money),
        ("total_users", "用户数", humanize_count),
        ("active_users", "  活跃", humanize_count),
        ("today_new_users", "  今日新增", humanize_count),
        ("total_accounts", "上游账户", humanize_count),
        ("normal_accounts", "  正常", humanize_count),
        ("error_accounts", "  异常", humanize_count),
        ("ratelimit_accounts", "  限流", humanize_count),
        ("overload_accounts", "  过载", humanize_count),
        ("rpm", "RPM", humanize_count),
        ("tpm", "TPM", humanize_count),
        ("average_duration_ms", "平均耗时", humanize_duration_ms),
        ("uptime", "运行时长", _humanize_seconds),
    ]
    for key, label, fmt in fields:
        if stats.get(key) is not None:
            print(f"  {label:<12} {fmt(stats[key])}")

    models = snapshot.get("models") or []
    if models:
        print("\n  模型 Top:")
        for m in models[:5]:
            name = (m.get("model") or "?")[:30]
            print(
                f"    {name:<30} "
                f"req {humanize_count(m.get('requests') or 0):>7}  "
                f"tok {humanize_count(m.get('total_tokens') or 0):>7}  "
                f"cost {humanize_money(m.get('cost') or 0):>9}"
            )


def _print_admin_accounts(
    items: list[dict[str, Any]],
    today: dict[str, Any],
    usage_map: Optional[dict[int, dict[str, Any]]] = None,
) -> None:
    today_map = (today.get("stats") or today) if isinstance(today, dict) else {}
    usage_map = usage_map or {}
    print(f"\n== 上游账户 ({len(items)}) ==")
    header = (
        f"{'name':<22} {'platform':<10} {'type':<14} {'status':<10} "
        f"{'5h window':<{ACCOUNT_WINDOW_COL_WIDTH}} {'7d window':<{ACCOUNT_WINDOW_COL_WIDTH}} "
        f"{'today req':>9} {'today tok':>10} {'today cost':>10} "
        f"{'concur':>7} {'sess/rpm':<16} {'last used':<19}"
    )
    print(f"  {header}")
    print(f"  {'-' * len(header)}")
    for a in items:
        st = today_map.get(str(a.get("id"))) or today_map.get(a.get("id")) or {}
        usage = usage_map.get(a.get("id")) if a.get("id") is not None else None
        print(
            f"  {(a.get('name') or '')[:22]:<22} "
            f"{(a.get('platform') or '')[:10]:<10} "
            f"{(a.get('type') or '')[:14]:<14} "
            f"{(a.get('status') or '')[:10]:<10} "
            f"{_account_5h_window(a, usage)[:ACCOUNT_WINDOW_COL_WIDTH]:<{ACCOUNT_WINDOW_COL_WIDTH}} "
            f"{_account_seven_day_window(a, usage)[:ACCOUNT_WINDOW_COL_WIDTH]:<{ACCOUNT_WINDOW_COL_WIDTH}} "
            f"{humanize_count(st.get('requests') or 0):>9} "
            f"{humanize_count(st.get('tokens') or 0):>10} "
            f"{humanize_money(st.get('cost') or 0):>10} "
            f"{_account_concurrency_display(a):>7} "
            f"{_account_sessions_rpm(a)[:16]:<16} "
            f"{_last_used_short(a.get('last_used_at')):<19}"
        )


USER_PERIODS = ("today", "yesterday", "week", "month", "all")

USER_PERIOD_LABELS = {
    "today": "今日",
    "yesterday": "昨日",
    "week": "7天",
    "month": "30天",
    "all": "全部",
}

USER_SORT_KEYS = ("today", "yesterday", "week", "month", "all", "email")

USER_SORT_LABELS = {
    "today": "今日↓",
    "yesterday": "昨日↓",
    "week": "7天↓",
    "month": "30天↓",
    "all": "全部↓",
    "email": "邮箱↑",
}


def _user_sort_key(key: str):
    if key == "email":
        return lambda u: ((u.get("email") or "").lower(), u.get("user_id") or 0)
    field = f"{key}_cost"
    return lambda u: -float(u.get(field) or 0)


def _sort_users(items: list[dict[str, Any]], sort_by: str) -> list[dict[str, Any]]:
    return sorted(items, key=_user_sort_key(sort_by))


async def _fetch_users_multi(
    client: "Client", limit: int = 200
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
    """并发拉取 USER_PERIODS 中每个时段的 ranking，按 email/user_id 合并。

    返回 (users, totals)：
    - users[i] 形如 {'email','user_id','today_cost','today_requests','today_tokens',...}
    - totals[period] = {'actual_cost','requests','tokens'}
    """
    pairs = [(p, *period_range(p)) for p in USER_PERIODS]
    rankings = await asyncio.gather(*[
        client.admin_users_ranking(s, e, limit=limit) for _, s, e in pairs
    ])
    merged: dict[str, dict[str, Any]] = {}
    totals: dict[str, dict[str, float]] = {}
    for (p, _s, _e), ranking in zip(pairs, rankings):
        totals[p] = {
            "actual_cost": float(ranking.get("total_actual_cost") or 0),
            "requests": float(ranking.get("total_requests") or 0),
            "tokens": float(ranking.get("total_tokens") or 0),
        }
        for u in ranking.get("ranking") or []:
            key = (u.get("email") or "") or f"#{u.get('user_id')}"
            entry = merged.setdefault(
                key, {"email": u.get("email") or "", "user_id": u.get("user_id")}
            )
            entry[f"{p}_cost"] = float(u.get("actual_cost") or 0)
            entry[f"{p}_requests"] = u.get("requests") or 0
            entry[f"{p}_tokens"] = u.get("tokens") or 0
    return list(merged.values()), totals


def _print_admin_users(
    items: list[dict[str, Any]],
    totals: dict[str, dict[str, float]],
    sort_by: str = "today",
) -> None:
    sort_label = USER_SORT_LABELS.get(sort_by, sort_by)
    print(f"\n== 用户消费排行 (sort={sort_label}, 共 {len(items)}) ==")
    totals_line = "  合计  " + "  ".join(
        f"{USER_PERIOD_LABELS[p]} {humanize_money((totals.get(p) or {}).get('actual_cost') or 0)}"
        for p in USER_PERIODS
    )
    print(totals_line)
    header = (
        f"{'#':>3}  {'email':<32} "
        f"{'今日':>10} {'昨日':>10} {'7天':>10} {'30天':>10} {'全部':>10}"
    )
    print(f"  {header}")
    print(f"  {'-' * len(header)}")
    for i, u in enumerate(items, 1):
        print(
            f"  {i:>3}  "
            f"{(u.get('email') or '')[:32]:<32} "
            f"{humanize_money(u.get('today_cost') or 0):>10} "
            f"{humanize_money(u.get('yesterday_cost') or 0):>10} "
            f"{humanize_money(u.get('week_cost') or 0):>10} "
            f"{humanize_money(u.get('month_cost') or 0):>10} "
            f"{humanize_money(u.get('all_cost') or 0):>10}"
        )


def _subscription_user_label(sub: dict[str, Any]) -> str:
    user = sub.get("user") or {}
    email = user.get("email") or ""
    if email:
        return email[:32]
    uid = sub.get("user_id")
    return f"#{uid}" if uid else "-"


def _subscription_group_label(sub: dict[str, Any]) -> str:
    group = sub.get("group") or {}
    name = group.get("name")
    if name:
        return str(name)[:18]
    gid = sub.get("group_id")
    return f"#{gid}" if gid else "-"


def _subscription_window_cell(sub: dict[str, Any], used_key: str, limit_key: str) -> str:
    used = sub.get(used_key)
    group = sub.get("group") or {}
    limit = group.get(limit_key)
    has_limit = limit not in (None, 0, 0.0)
    has_usage = used not in (None, 0, 0.0)
    if not has_limit and not has_usage:
        return "-"
    return _format_used_limit(used, limit)


SUB_SORT_KEYS = ("expires_at", "daily_usage", "weekly_usage", "monthly_usage", "id", "email")

SUB_SORT_LABELS = {
    "daily_usage": "今日↓",
    "weekly_usage": "7天↓",
    "monthly_usage": "30天↓",
    "expires_at": "到期↑",
    "id": "ID↑",
    "email": "邮箱↑",
}


def _sub_sort_key(key: str):
    """返回适合 sorted(key=...) 的函数。"""
    def by_id(s: dict[str, Any]) -> Any:
        return s.get("id") or 0

    def by_email(s: dict[str, Any]) -> Any:
        return ((s.get("user") or {}).get("email") or "").lower()

    def by_expires(s: dict[str, Any]) -> Any:
        return s.get("expires_at") or ""

    def by_usage(usage_field: str):
        def _k(s: dict[str, Any]) -> Any:
            return -(float(s.get(usage_field) or 0))
        return _k

    mapping = {
        "id": by_id,
        "email": by_email,
        "expires_at": by_expires,
        "daily_usage": by_usage("daily_usage_usd"),
        "weekly_usage": by_usage("weekly_usage_usd"),
        "monthly_usage": by_usage("monthly_usage_usd"),
    }
    return mapping.get(key, by_expires)


def _sort_subscriptions(items: list[dict[str, Any]], sort_by: str) -> list[dict[str, Any]]:
    return sorted(items, key=_sub_sort_key(sort_by))


def _search_plain(value: Any) -> str:
    if hasattr(value, "plain"):
        return str(value.plain)
    return str(value or "")


def _row_matches_search(values: list[Any], query: str) -> bool:
    needle = query.strip().casefold()
    if not needle:
        return True
    return any(needle in _search_plain(value).casefold() for value in values)


def _highlight_search_text(value: Any, query: str):
    if not query.strip():
        return value
    from rich.text import Text

    text = value.copy() if isinstance(value, Text) else Text(_search_plain(value))
    pattern = re.escape(query.strip())
    text.highlight_regex(f"(?i){pattern}", "black on yellow")
    return text


# ----- Color helpers (used by Admin TUI) ---------------------------------------

_STATUS_COLORS = {
    "active": "green",
    "normal": "green",
    "enabled": "green",
    "expired": "red",
    "error": "red",
    "failed": "red",
    "disabled": "bright_black",
    "inactive": "bright_black",
    "ratelimit": "yellow",
    "rate_limited": "yellow",
    "rate-limited": "yellow",
    "overload": "magenta",
    "overloaded": "magenta",
    "paused": "yellow",
    "suspended": "yellow",
}


def _status_style(status: Any) -> str:
    return _STATUS_COLORS.get(str(status or "").lower(), "")


def _util_style(util_pct: Optional[float]) -> str:
    if util_pct is None:
        return ""
    if util_pct >= 100:
        return "bold red"
    if util_pct >= 80:
        return "yellow"
    if util_pct >= 50:
        return "cyan"
    return "green"


def _extract_util(text: str) -> Optional[float]:
    """从形如 '$10.00 / $100.00 (10%)' 的字符串里提取 utilization 百分比。"""
    import re
    m = re.search(r"\((\d+(?:\.\d+)?)%\)", text or "")
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _expires_style(ts: Any) -> str:
    dt = _parse_iso(ts)
    if dt is None:
        return ""
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    delta = dt - now
    if delta.total_seconds() < 0:
        return "bold red"
    if delta.days < 3:
        return "red"
    if delta.days < 14:
        return "yellow"
    return ""


def _print_admin_subscriptions(
    data: dict[str, Any],
    status: str = "active",
    sort_by: str = "daily_usage",
) -> None:
    items = data.get("items") or []
    pagination = data.get("pagination") or {}
    total = pagination.get("total") or len(items)
    print(f"\n== 订阅管理 (status={status}, sort={sort_by}, 共 {total}, 显示 {len(items)}) ==")
    header = (
        f"{'#':>4}  {'用户':<32} {'分组':<18} {'状态':<8} "
        f"{'日 (used/limit)':<26} {'周 (used/limit)':<26} {'月 (used/limit)':<26} "
        f"{'到期':<28}"
    )
    print(f"  {header}")
    print(f"  {'-' * len(header)}")
    for sub in items:
        print(
            f"  {sub.get('id', '?'):>4}  "
            f"{_subscription_user_label(sub):<32} "
            f"{_subscription_group_label(sub):<18} "
            f"{(sub.get('status') or '')[:8]:<8} "
            f"{_subscription_window_cell(sub, 'daily_usage_usd', 'daily_limit_usd')[:26]:<26} "
            f"{_subscription_window_cell(sub, 'weekly_usage_usd', 'weekly_limit_usd')[:26]:<26} "
            f"{_subscription_window_cell(sub, 'monthly_usage_usd', 'monthly_limit_usd')[:26]:<26} "
            f"{_expires_short(sub.get('expires_at')):<28}"
        )


# ===== Admin TUI ==============================================================

def run_admin_tui(cfg: dict[str, str]) -> None:
    from rich.markup import escape
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import VerticalScroll
    from textual.widgets import ContentSwitcher, DataTable, Footer, Header, Input, Static, Tab, Tabs

    PERIOD_LABELS = {"today": "今天", "yesterday": "昨天", "week": "7 天", "month": "30 天", "all": "全部"}

    def styled(text: str, style: str = "") -> Text:
        return Text(text, style=style) if style else Text(text)

    def status_cell(value: Any, width: int = 10) -> Text:
        s = (str(value or "") or "-")[:width]
        return styled(s, _status_style(value))

    def window_cell(rendered: str, max_width: int = ACCOUNT_WINDOW_COL_WIDTH) -> Text:
        util = _extract_util(rendered)
        return styled(rendered[:max_width], _util_style(util))

    def expires_cell(ts: Any) -> Text:
        return styled(_expires_short(ts), _expires_style(ts))

    def cost_cell(value: Any, threshold: float = 0.0) -> Text:
        try:
            v = float(value or 0)
        except (TypeError, ValueError):
            v = 0.0
        style = "yellow" if v > threshold else "bright_black"
        return styled(humanize_money(v), style)

    def count_cell(value: Any) -> Text:
        try:
            v = float(value or 0)
        except (TypeError, ValueError):
            v = 0.0
        return styled(humanize_count(v), "" if v > 0 else "bright_black")

    class AdminApp(App):
        CSS = """
        Screen { background: $surface; }
        Header { background: $primary 30%; color: $text; }
        #dashboard_view {
            padding: 1 2;
            margin: 0 1;
            border: round $primary;
        }
        #dashboard_text { color: $text; }
        Tabs {
            margin: 0 1;
            background: $boost;
        }
        Tabs > #tabs-list > Tab {
            color: $text-muted;
        }
        Tabs > #tabs-list > Tab.-active {
            color: $accent;
            text-style: bold;
        }
        DataTable {
            margin: 0 1;
            height: 1fr;
            background: $surface;
        }
        DataTable > .datatable--header {
            background: $primary 40%;
            color: $text;
            text-style: bold;
        }
        DataTable > .datatable--cursor {
            background: $accent 40%;
        }
        #search {
            margin: 0 1;
            height: 3;
        }
        #search.search-hidden {
            display: none;
        }
        #status {
            dock: bottom;
            height: 1;
            background: $boost;
            color: $text-muted;
            padding: 0 2;
        }
        Footer { background: $primary 30%; }
        """
        BINDINGS = [
            Binding("q", "quit", "退出"),
            Binding("/", "start_search", "搜索", show=False),
            Binding("r", "refresh", "刷新"),
            Binding("d", "set_view('dashboard')", "Dashboard"),
            Binding("a", "set_view('accounts')", "账户"),
            Binding("u", "set_view('users')", "用户"),
            Binding("s", "set_view('subscriptions')", "订阅"),
            Binding("S", "toggle_sub_status", "订阅状态"),
            Binding("o", "cycle_sort", "排序"),
            Binding("1", "set_period('today')", "今天"),
            Binding("2", "set_period('yesterday')", "昨天"),
            Binding("3", "set_period('week')", "7 天"),
            Binding("4", "set_period('month')", "30 天"),
            Binding("5", "set_period('all')", "全部"),
            Binding("e", "sort_by('email')", "按邮箱"),
            Binding("i", "sort_by('id')", "按ID", show=False),
            Binding("x", "sort_by('expires_at')", "按到期", show=False),
        ]

        view = "dashboard"
        period = "today"
        sub_status = "active"
        sub_sort_by = "daily_usage"
        user_sort_by = "today"

        def __init__(self, cfg: dict[str, str]):
            super().__init__()
            self.cfg = cfg
            self.client = Client(cfg["base_url"], cfg["email"], cfg["password"], cfg["timezone"])
            self._users_cache: list[dict[str, Any]] = []
            self._users_totals: dict[str, dict[str, float]] = {}
            self._subscriptions_cache: dict[str, Any] = {}
            self.search_query = ""
            self.searching = False

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield Tabs(
                Tab("Dashboard (d)", id="dashboard"),
                Tab("Accounts (a)", id="accounts"),
                Tab("Users (u)", id="users"),
                Tab("Subscriptions (s)", id="subscriptions"),
            )
            with ContentSwitcher(initial="dashboard_view", id="switcher"):
                yield VerticalScroll(Static("加载中...", id="dashboard_text"), id="dashboard_view")
                acc_tbl: DataTable = DataTable(
                    id="accounts_view", zebra_stripes=True, cursor_type="row",
                    cursor_foreground_priority="renderable",
                )
                acc_tbl.add_columns(
                    "名称", "平台", "类型", "状态", "5h 窗口", "7d 窗口",
                    "今日请求", "今日 Token", "今日成本", "并发", "Sess·RPM", "最后使用",
                )
                yield acc_tbl
                usr_tbl: DataTable = DataTable(
                    id="users_view", zebra_stripes=True, cursor_type="row",
                    cursor_foreground_priority="renderable",
                )
                usr_tbl.add_columns(
                    "#", "邮箱 [e]", "今天 [1]", "昨天 [2]", "7天 [3]", "30天 [4]", "全部 [5]",
                )
                yield usr_tbl
                sub_tbl: DataTable = DataTable(
                    id="subscriptions_view", zebra_stripes=True, cursor_type="row",
                    cursor_foreground_priority="renderable",
                )
                sub_tbl.add_columns(
                    "ID [i]", "用户 [e]", "分组", "状态", "日 [1]", "周 [3]", "月 [4]", "到期 [x]",
                )
                yield sub_tbl
            yield Input(placeholder="/ 搜索当前 Users/Subscriptions", id="search", compact=True, classes="search-hidden")
            yield Static(f"管理员 {self.cfg['email']}  ·  {self.cfg['base_url']}", id="status")
            yield Footer()

        async def on_mount(self) -> None:
            self.title = "sub2api 管理员面板"
            self.sub_title = self.cfg["email"]
            await self._refresh_data()

        async def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:  # noqa: F821
            tab_id = event.tab.id if event.tab else None
            if tab_id and tab_id != self.view:
                self.view = tab_id
                self.query_one("#switcher", ContentSwitcher).current = f"{tab_id}_view"
                self._stop_search(focus_table=False)
                await self._refresh_data()

        def _search_enabled(self) -> bool:
            return self.view in {"users", "subscriptions"}

        def _match_count(self) -> int:
            if not self.search_query.strip():
                return 0
            if self.view == "users":
                return sum(
                    1
                    for u in self._users_cache
                    if _row_matches_search(self._user_search_values(u), self.search_query)
                )
            if self.view == "subscriptions":
                return sum(
                    1
                    for sub in self._subscriptions_cache.get("items") or []
                    if _row_matches_search(self._subscription_search_values(sub), self.search_query)
                )
            return 0

        def _search_suffix(self) -> str:
            if not self._search_enabled():
                return ""
            if self.search_query.strip():
                query = escape(self.search_query)
                return f" · 搜索 [b yellow]/{query}[/] ({self._match_count()} 条) · [dim]/[/]编辑 [dim]Esc[/]清除"
            return " · [dim]/[/]搜索"

        def _stop_search(self, focus_table: bool = True) -> None:
            self.searching = False
            search = self.query_one("#search", Input)
            search.add_class("search-hidden")
            if focus_table and self._search_enabled():
                self.set_focus(self.query_one(f"#{self.view}_view", DataTable))

        async def action_start_search(self) -> None:
            if not self._search_enabled():
                return
            self.searching = True
            search = self.query_one("#search", Input)
            search.value = self.search_query
            search.remove_class("search-hidden")
            self.set_focus(search)
            self._update_status_hint()

        async def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id != "search":
                return
            self.search_query = event.value
            if self.view == "users":
                self._render_users(self._users_cache, self._users_totals)
            elif self.view == "subscriptions":
                self._render_subscriptions(self._subscriptions_cache)
            self._update_status_hint()

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            if event.input.id == "search":
                self._stop_search()
                self._update_status_hint()

        async def on_key(self, event) -> None:
            if event.key == "escape" and self._clear_search():
                event.stop()

        async def action_set_view(self, v: str) -> None:
            self.query_one(Tabs).active = v

        def _clear_search(self) -> bool:
            if not self._search_enabled() or (not self.searching and not self.search_query):
                return False
            self.search_query = ""
            search = self.query_one("#search", Input)
            search.value = ""
            self._stop_search()
            if self.view == "users":
                self._render_users(self._users_cache, self._users_totals)
            elif self.view == "subscriptions":
                self._render_subscriptions(self._subscriptions_cache)
            self._update_status_hint()
            return True

        async def action_set_period(self, p: str) -> None:
            if self.view == "users":
                if p in USER_SORT_KEYS and self.user_sort_by != p:
                    self.user_sort_by = p
                    self._render_users(self._users_cache, self._users_totals)
                    self._update_status_hint()
                return
            if self.view == "subscriptions":
                sub_map = {"today": "daily_usage", "week": "weekly_usage", "month": "monthly_usage"}
                sub_key = sub_map.get(p)
                if sub_key and self.sub_sort_by != sub_key:
                    self.sub_sort_by = sub_key
                    await self._refresh_data()
                return
            if p != self.period:
                self.period = p
                await self._refresh_data()

        async def action_sort_by(self, key: str) -> None:
            if self.view == "users":
                if key in USER_SORT_KEYS and self.user_sort_by != key:
                    self.user_sort_by = key
                    self._render_users(self._users_cache, self._users_totals)
                    self._update_status_hint()
            elif self.view == "subscriptions":
                if key in SUB_SORT_KEYS and self.sub_sort_by != key:
                    self.sub_sort_by = key
                    await self._refresh_data()

        async def action_refresh(self) -> None:
            await self._refresh_data()

        async def action_toggle_sub_status(self) -> None:
            order = ["active", "expired", "paused", "all"]
            try:
                idx = order.index(self.sub_status)
            except ValueError:
                idx = -1
            self.sub_status = order[(idx + 1) % len(order)]
            if self.view == "subscriptions":
                await self._refresh_data()
            else:
                self._update_status_hint()

        async def action_cycle_sort(self) -> None:
            if self.view == "subscriptions":
                order = list(SUB_SORT_KEYS)
                idx = order.index(self.sub_sort_by) if self.sub_sort_by in order else -1
                self.sub_sort_by = order[(idx + 1) % len(order)]
                await self._refresh_data()
            elif self.view == "users":
                order = list(USER_SORT_KEYS)
                idx = order.index(self.user_sort_by) if self.user_sort_by in order else -1
                self.user_sort_by = order[(idx + 1) % len(order)]
                self._render_users(self._users_cache, self._users_totals)
                self._update_status_hint()
            else:
                self._update_status_hint()

        def _update_status_hint(self) -> None:
            start, end = period_range(self.period)
            if self.view == "subscriptions":
                sort_label = SUB_SORT_LABELS.get(self.sub_sort_by, self.sub_sort_by)
                hint = (
                    f"\\[[b]{self.sub_status}[/]/[b]{sort_label}[/]\\] "
                    f"· [dim]1[/]日 [dim]3[/]周 [dim]4[/]月 [dim]e[/]邮箱 [dim]i[/]ID [dim]x[/]到期 [dim]o[/]循环 [dim]S[/]状态 "
                    f"· r 刷新 · q 退出"
                )
            elif self.view == "users":
                sort_label = USER_SORT_LABELS.get(self.user_sort_by, self.user_sort_by)
                hint = (
                    f"排序 \\[[b]{sort_label}[/]\\] "
                    f"· [dim]1[/]今天 [dim]2[/]昨天 [dim]3[/]7天 [dim]4[/]30天 [dim]5[/]全部 [dim]e[/]邮箱 [dim]o[/]循环 "
                    f"· r 刷新 · q 退出"
                )
            else:
                hint = (
                    f"d Dashboard · a 账户 · u 用户 · s 订阅 · "
                    f"1/2/3/4/5 时段 · r 刷新 · q 退出"
                )
            self.query_one("#status", Static).update(
                f"管理员 [b cyan]{self.cfg['email']}[/]  ·  "
                f"[dim]{start} ~ {end}[/] ([magenta]{PERIOD_LABELS[self.period]}[/])  ·  {hint}{self._search_suffix()}"
            )

        async def _refresh_data(self) -> None:
            start, end = period_range(self.period)
            status = self.query_one("#status", Static)
            status.update(
                f"管理员 [b cyan]{self.cfg['email']}[/]  ·  "
                f"[dim]{start} ~ {end}[/] ([magenta]{PERIOD_LABELS[self.period]}[/])  ·  [yellow]加载中...[/]"
            )
            try:
                active = self.query_one(f"#{self.view}_view")
            except Exception:
                active = None
            if active is not None:
                active.loading = True
            try:
                if self.view == "dashboard":
                    stats = await self.client.admin_dashboard_stats()
                    snapshot = await self.client.admin_dashboard_snapshot(start, end)
                    self.query_one("#dashboard_text", Static).update(
                        self._render_dashboard(stats, snapshot, start, end)
                    )
                elif self.view == "accounts":
                    data = await self.client.admin_accounts()
                    items = data.get("items") or []
                    ids = [a["id"] for a in items if a.get("id") is not None]
                    if ids:
                        today, usage_map = await asyncio.gather(
                            self.client.admin_accounts_today_batch(ids),
                            self.client.admin_account_usage_batch(items),
                        )
                    else:
                        today, usage_map = {}, {}
                    self._render_accounts(items, today, usage_map)
                elif self.view == "users":
                    items, totals = await _fetch_users_multi(self.client)
                    self._render_users(items, totals)
                elif self.view == "subscriptions":
                    status_filter = None if self.sub_status == "all" else self.sub_status
                    data = await self.client.admin_subscriptions(
                        page=1, page_size=200, status=status_filter,
                    )
                    data["items"] = _sort_subscriptions(data.get("items") or [], self.sub_sort_by)
                    self._subscriptions_cache = data
                    self._render_subscriptions(data)
            except APIError as e:
                status.update(f"[red]错误: {e}[/]")
                return
            finally:
                if active is not None:
                    active.loading = False

            self._update_status_hint()

        @staticmethod
        def _render_dashboard(stats: dict[str, Any], snapshot: dict[str, Any], start: str, end: str) -> str:
            today_req = humanize_count(stats.get("today_requests") or 0)
            today_in = humanize_count(stats.get("today_input_tokens") or 0)
            today_out = humanize_count(stats.get("today_output_tokens") or 0)
            today_cost = humanize_money(stats.get("today_cost") or 0)
            today_actual = humanize_money(stats.get("today_actual_cost") or 0)
            tot_req = humanize_count(stats.get("total_requests") or 0)
            tot_tok = humanize_count(stats.get("total_tokens") or 0)
            tot_cost = humanize_money(stats.get("total_cost") or 0)
            tot_actual = humanize_money(stats.get("total_actual_cost") or 0)
            users = humanize_count(stats.get("total_users") or 0)
            new_users = humanize_count(stats.get("today_new_users") or 0)
            active = humanize_count(stats.get("active_users") or 0)
            accts = humanize_count(stats.get("total_accounts") or 0)
            normal = humanize_count(stats.get("normal_accounts") or 0)
            err = humanize_count(stats.get("error_accounts") or 0)
            rl = humanize_count(stats.get("ratelimit_accounts") or 0)
            ovl = humanize_count(stats.get("overload_accounts") or 0)
            rpm = humanize_count(stats.get("rpm") or 0)
            tpm = humanize_count(stats.get("tpm") or 0)
            dur = humanize_duration_ms(stats.get("average_duration_ms") or 0)
            uptime = _humanize_seconds(stats.get("uptime") or 0)

            lines = [
                f"[b]今日[/]   请求 [cyan]{today_req}[/]  ([dim]in[/] {today_in} · [dim]out[/] {today_out})  "
                f"成本 [yellow]{today_cost}[/]   实际 [yellow]{today_actual}[/]",
                f"[b]累计[/]   请求 [cyan]{tot_req}[/]   Token [cyan]{tot_tok}[/]   "
                f"成本 [yellow]{tot_cost}[/]   实际 [yellow]{tot_actual}[/]",
                f"[b]用户[/]   总数 [cyan]{users}[/]   ([dim]活跃[/] {active} · [dim]今日新增[/] {new_users})",
                f"[b]账户[/]   总数 [cyan]{accts}[/]   ([dim]正常[/] {normal} · [dim]异常[/] {err} · "
                f"[dim]限流[/] {rl} · [dim]过载[/] {ovl})",
                f"[b]性能[/]   RPM [magenta]{rpm}[/]   TPM [magenta]{tpm}[/]   平均耗时 [magenta]{dur}[/]   "
                f"运行 [magenta]{uptime}[/]",
            ]

            models = snapshot.get("models") or []
            if models:
                lines.append("")
                lines.append(f"[b]模型 Top (范围 {start} ~ {end})[/]")
                for m in models[:5]:
                    name = (m.get("model") or "?")[:30]
                    lines.append(
                        f"  {name:<30}  req [cyan]{humanize_count(m.get('requests') or 0):>7}[/]  "
                        f"tok [cyan]{humanize_count(m.get('total_tokens') or 0):>7}[/]  "
                        f"cost [yellow]{humanize_money(m.get('cost') or 0):>9}[/]"
                    )
            return "\n".join(lines)

        def _render_accounts(
            self,
            items: list[dict[str, Any]],
            today: dict[str, Any],
            usage_map: Optional[dict[int, dict[str, Any]]] = None,
        ) -> None:
            tbl = self.query_one("#accounts_view", DataTable)
            tbl.clear()
            today_map = (today.get("stats") if isinstance(today, dict) else None) or today or {}
            usage_map = usage_map or {}
            for a in items:
                key = str(a.get("id"))
                st = today_map.get(key) or today_map.get(a.get("id")) or {}
                usage = usage_map.get(a.get("id")) if a.get("id") is not None else None
                tbl.add_row(
                    styled((a.get("name") or "")[:28], "bold"),
                    styled((a.get("platform") or "")[:10], "cyan"),
                    styled((a.get("type") or "")[:14], "dim"),
                    status_cell(a.get("status"), 10),
                    window_cell(_account_5h_window(a, usage), ACCOUNT_WINDOW_COL_WIDTH),
                    window_cell(_account_seven_day_window(a, usage), ACCOUNT_WINDOW_COL_WIDTH),
                    count_cell(st.get("requests")),
                    count_cell(st.get("tokens")),
                    cost_cell(st.get("cost")),
                    styled(_account_concurrency_display(a)),
                    styled(_account_sessions_rpm(a)[:16], "dim"),
                    styled(_last_used_short(a.get("last_used_at")), "dim"),
                )

        def _render_users(
            self,
            items: list[dict[str, Any]],
            totals: dict[str, dict[str, float]],
        ) -> None:
            self._users_cache = items
            self._users_totals = totals
            tbl = self.query_one("#users_view", DataTable)
            tbl.clear(columns=True)
            cur = self.user_sort_by
            ascending = {"email"}

            def lbl(key: str, base: str) -> str:
                if cur != key:
                    return base
                return f"{base} {'↑' if key in ascending else '↓'}"

            tbl.add_columns(
                "#",
                lbl("email", "邮箱 [e]"),
                lbl("today", "今天 [1]"),
                lbl("yesterday", "昨天 [2]"),
                lbl("week", "7天 [3]"),
                lbl("month", "30天 [4]"),
                lbl("all", "全部 [5]"),
            )
            for i, u in enumerate(_sort_users(items, cur), 1):
                if not _row_matches_search(self._user_search_values(u), self.search_query):
                    continue
                rank_style = "bold yellow" if i <= 3 else "dim"
                tbl.add_row(
                    _highlight_search_text(styled(str(i), rank_style), self.search_query),
                    _highlight_search_text(styled((u.get("email") or "")[:32], "cyan"), self.search_query),
                    _highlight_search_text(cost_cell(u.get("today_cost")), self.search_query),
                    _highlight_search_text(cost_cell(u.get("yesterday_cost")), self.search_query),
                    _highlight_search_text(cost_cell(u.get("week_cost")), self.search_query),
                    _highlight_search_text(cost_cell(u.get("month_cost")), self.search_query),
                    _highlight_search_text(cost_cell(u.get("all_cost")), self.search_query),
                )

        def _render_subscriptions(self, data: dict[str, Any]) -> None:
            self._subscriptions_cache = data
            tbl = self.query_one("#subscriptions_view", DataTable)
            tbl.clear(columns=True)
            cur = self.sub_sort_by
            ascending = {"id", "email", "expires_at"}

            def lbl(key: str, base: str) -> str:
                if cur != key:
                    return base
                return f"{base} {'↑' if key in ascending else '↓'}"

            tbl.add_columns(
                lbl("id", "ID [i]"),
                lbl("email", "用户 [e]"),
                "分组",
                "状态",
                lbl("daily_usage", "日 [1]"),
                lbl("weekly_usage", "周 [3]"),
                lbl("monthly_usage", "月 [4]"),
                lbl("expires_at", "到期 [x]"),
            )
            for sub in data.get("items") or []:
                if not _row_matches_search(self._subscription_search_values(sub), self.search_query):
                    continue
                tbl.add_row(
                    _highlight_search_text(styled(str(sub.get("id", "")), "dim"), self.search_query),
                    _highlight_search_text(styled(_subscription_user_label(sub), "cyan"), self.search_query),
                    _highlight_search_text(styled(_subscription_group_label(sub), "magenta"), self.search_query),
                    _highlight_search_text(status_cell(sub.get("status"), 10), self.search_query),
                    _highlight_search_text(
                        window_cell(_subscription_window_cell(sub, "daily_usage_usd", "daily_limit_usd"), 26),
                        self.search_query,
                    ),
                    _highlight_search_text(
                        window_cell(_subscription_window_cell(sub, "weekly_usage_usd", "weekly_limit_usd"), 26),
                        self.search_query,
                    ),
                    _highlight_search_text(
                        window_cell(_subscription_window_cell(sub, "monthly_usage_usd", "monthly_limit_usd"), 26),
                        self.search_query,
                    ),
                    _highlight_search_text(expires_cell(sub.get("expires_at")), self.search_query),
                )

        @staticmethod
        def _user_search_values(u: dict[str, Any]) -> list[Any]:
            return [
                u.get("email") or "",
                humanize_money(u.get("today_cost") or 0),
                humanize_money(u.get("yesterday_cost") or 0),
                humanize_money(u.get("week_cost") or 0),
                humanize_money(u.get("month_cost") or 0),
                humanize_money(u.get("all_cost") or 0),
            ]

        @staticmethod
        def _subscription_search_values(sub: dict[str, Any]) -> list[Any]:
            return [
                str(sub.get("id", "")),
                _subscription_user_label(sub),
                _subscription_group_label(sub),
                str(sub.get("status") or ""),
                _subscription_window_cell(sub, "daily_usage_usd", "daily_limit_usd"),
                _subscription_window_cell(sub, "weekly_usage_usd", "weekly_limit_usd"),
                _subscription_window_cell(sub, "monthly_usage_usd", "monthly_limit_usd"),
                _expires_short(sub.get("expires_at")),
            ]

        async def on_unmount(self) -> None:
            await self.client.aclose()

    AdminApp(cfg).run()


# ===== Profile management =====================================================

def _profile_ns(namespace: str) -> tuple[str, str]:
    """(default_key, setup_hint) for the given profile namespace."""
    if namespace == "admin_profiles":
        return "admin_default", "sub2api-usage admin setup"
    return "default", "sub2api-usage setup"


def cmd_profiles_list(cfg: dict[str, Any], namespace: str = "profiles") -> int:
    default_key, setup_hint = _profile_ns(namespace)
    profiles = cfg.get(namespace) or {}
    if not profiles:
        print(f"(无 profile，先运行 '{setup_hint}')")
        return 0
    default = cfg.get(default_key)
    name_w = max(len(n) for n in profiles)
    email_w = max(len(p.get("email", "")) for p in profiles.values())
    for n, p in profiles.items():
        marker = "*" if n == default else " "
        print(f"  {marker} {n:<{name_w}}  {p.get('email', ''):<{email_w}}  {p.get('base_url', '')}")
    print(f"\n* = default ({default})")
    return 0


def cmd_profiles_use(cfg: dict[str, Any], name: str, namespace: str = "profiles") -> int:
    default_key, _ = _profile_ns(namespace)
    profiles = cfg.get(namespace) or {}
    if name not in profiles:
        print(f"[错误] profile '{name}' 不存在；现有: {', '.join(profiles) or '(空)'}", file=sys.stderr)
        return 1
    cfg[default_key] = name
    save_config(cfg)
    print(f"default 已切换到 '{name}'")
    return 0


def cmd_profiles_remove(cfg: dict[str, Any], name: str, namespace: str = "profiles") -> int:
    default_key, _ = _profile_ns(namespace)
    profiles = dict(cfg.get(namespace) or {})
    if name not in profiles:
        print(f"[错误] profile '{name}' 不存在", file=sys.stderr)
        return 1
    if input(f"确认删除 profile '{name}' ? [y/N] ").strip().lower() not in ("y", "yes"):
        print("取消")
        return 0
    del profiles[name]
    cfg[namespace] = profiles
    if cfg.get(default_key) == name:
        cfg[default_key] = next(iter(profiles), "")
        if cfg[default_key]:
            print(f"  顺便把 default 切到了 '{cfg[default_key]}'")
    save_config(cfg)
    print(f"已删除 profile '{name}'")
    return 0


# ===== CLI ====================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="sub2api 用量查询")
    p.add_argument("-P", "--profile", help="使用指定的 profile (默认: 配置里的 default)")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("setup", help="(重新) 配置账号信息")
    sp.add_argument("name", nargs="?", help="profile 名称，省略则更新当前 default")

    pp = sub.add_parser("print", help="非交互打印 (脚本/管道用)")
    pp.add_argument("--period", default=DEFAULT_PERIOD, choices=[k for k, _ in PERIODS])
    pp.add_argument("--list", action="store_true", help="同时拉取明细")
    pp.add_argument("--page", type=int, default=1)
    pp.add_argument("--page-size", type=int, default=20)
    pp.add_argument("--json", action="store_true")

    pf = sub.add_parser("profiles", help="管理 profile (多账号/多后台)")
    pf_sub = pf.add_subparsers(dest="action")
    pf_sub.add_parser("list", help="列出所有 profile")
    pu = pf_sub.add_parser("use", help="切换 default profile")
    pu.add_argument("name")
    prm = pf_sub.add_parser("remove", help="删除 profile")
    prm.add_argument("name")

    # --- admin 子命令组 ---
    ap = sub.add_parser("admin", help="管理员模式：查看账户余额、用户用量等")
    ap.add_argument(
        "--no-color", action="store_true",
        help="不强制开启颜色 (默认会清除 NO_COLOR 并设置 FORCE_COLOR=1，方便 TUI 显示)",
    )
    ap_sub = ap.add_subparsers(dest="admin_cmd")

    asp = ap_sub.add_parser("setup", help="(重新) 配置管理员账号")
    asp.add_argument("name", nargs="?", help="admin profile 名称")

    app_ = ap_sub.add_parser("print", help="非交互打印管理员视图")
    app_.add_argument("--view", default="dashboard", choices=ADMIN_VIEWS)
    app_.add_argument("--period", default="today", choices=[k for k, _ in PERIODS])
    app_.add_argument("--json", action="store_true")
    app_.add_argument("--status", default="active", choices=["active", "expired", "paused", "all"],
                      help="(subscriptions) 订阅状态过滤；默认 active")
    app_.add_argument("--sort-by", default=None, dest="sort_by",
                      choices=sorted(set(SUB_SORT_KEYS) | set(USER_SORT_KEYS)),
                      help="(subscriptions/users) 排序字段；不传则按 view 取默认 "
                           "(subscriptions=daily_usage, users=today)")

    apf = ap_sub.add_parser("profiles", help="管理 admin profile")
    apf_sub = apf.add_subparsers(dest="admin_action")
    apf_sub.add_parser("list", help="列出所有 admin profile")
    apu = apf_sub.add_parser("use", help="切换 admin default profile")
    apu.add_argument("name")
    aprm = apf_sub.add_parser("remove", help="删除 admin profile")
    aprm.add_argument("name")
    return p


def main() -> int:
    try:
        return _main()
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


def _can_prompt() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _missing_config_error(setup_cmd: str) -> int:
    print(f"未检测到配置；请先运行 '{setup_cmd}' 完成初始化。", file=sys.stderr)
    return 1


def _main() -> int:
    args = build_parser().parse_args()
    cfg = load_config()

    if args.cmd == "setup":
        asyncio.run(run_setup(cfg, args.name))
        return 0

    if args.cmd == "profiles":
        if cfg is None:
            print("还没有任何 profile，先运行 'sub2api-usage setup'", file=sys.stderr)
            return 1
        action = args.action or "list"
        if action == "list":
            return cmd_profiles_list(cfg)
        if action == "use":
            return cmd_profiles_use(cfg, args.name)
        if action == "remove":
            return cmd_profiles_remove(cfg, args.name)
        return 0

    if args.cmd == "admin":
        return _admin_main(args, cfg)

    if cfg is None:
        if not _can_prompt():
            return _missing_config_error("sub2api-usage setup")
        print("未检测到配置，进入引导...")
        cfg = asyncio.run(run_setup(None))

    try:
        _, profile = resolve_profile(cfg, args.profile)
    except APIError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 2

    if args.cmd == "print":
        try:
            asyncio.run(cmd_print(profile, args.period, args.list, args.page, args.page_size, args.json))
        except APIError as e:
            print(f"[错误] {e}", file=sys.stderr)
            return 1
        return 0

    run_tui(profile)
    return 0


def _admin_main(args: argparse.Namespace, cfg: Optional[dict[str, Any]]) -> int:
    if not getattr(args, "no_color", False):
        os.environ.pop("NO_COLOR", None)
        os.environ.setdefault("FORCE_COLOR", "1")
    ac = getattr(args, "admin_cmd", None) or ""

    if ac == "setup":
        asyncio.run(run_setup(cfg or {}, getattr(args, "name", None), namespace="admin_profiles"))
        return 0

    if ac == "profiles":
        if cfg is None or not (cfg.get("admin_profiles") or {}):
            print("还没有任何 admin profile，先运行 'sub2api-usage admin setup'", file=sys.stderr)
            return 1
        action = getattr(args, "admin_action", None) or "list"
        if action == "list":
            return cmd_profiles_list(cfg, "admin_profiles")
        if action == "use":
            return cmd_profiles_use(cfg, args.name, "admin_profiles")
        if action == "remove":
            return cmd_profiles_remove(cfg, args.name, "admin_profiles")
        return 0

    if cfg is None or not (cfg.get("admin_profiles") or {}):
        if not _can_prompt():
            return _missing_config_error("sub2api-usage admin setup")
        print("未检测到 admin 配置，进入引导...")
        cfg = asyncio.run(run_setup(cfg or {}, None, namespace="admin_profiles"))

    try:
        _, profile = resolve_profile(cfg, args.profile, namespace="admin_profiles")
    except APIError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 2

    if ac == "print":
        try:
            view = args.view
            raw_sort = getattr(args, "sort_by", None)
            sub_sort = raw_sort if raw_sort in SUB_SORT_KEYS else "daily_usage"
            user_sort = raw_sort if raw_sort in USER_SORT_KEYS else "today"
            asyncio.run(cmd_admin_print(
                profile, view, args.period, args.json,
                sub_status=getattr(args, "status", "active"),
                sub_sort_by=sub_sort,
                user_sort_by=user_sort,
            ))
        except APIError as e:
            print(f"[错误] {e}", file=sys.stderr)
            return 1
        return 0

    run_admin_tui(profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
