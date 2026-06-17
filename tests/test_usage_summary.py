import asyncio
import inspect
import unittest
from contextlib import redirect_stdout
from io import StringIO

import sub2api_usage


class FakeUsageClient:
    def __init__(self):
        self.calls = []

    async def stats(self, start, end):
        self.calls.append((start, end))
        return {
            "total_requests": len(self.calls),
            "total_tokens": len(self.calls) * 100,
            "total_input_tokens": len(self.calls) * 10,
            "total_output_tokens": len(self.calls) * 20,
            "total_cache_tokens": len(self.calls) * 30,
            "total_cost": len(self.calls) / 10,
            "total_actual_cost": len(self.calls) / 20,
            "average_duration_ms": len(self.calls) * 1000,
        }


class UsageSummaryTests(unittest.TestCase):
    def test_ordinary_summary_windows_are_fixed_to_stats_periods(self):
        self.assertEqual(
            sub2api_usage.ORDINARY_SUMMARY_PERIODS,
            ("today", "yesterday", "week", "month"),
        )

    def test_fetch_usage_summaries_fetches_each_window_without_request_list(self):
        client = FakeUsageClient()

        summaries = asyncio.run(sub2api_usage._fetch_usage_summaries(client))

        self.assertEqual([s["period"] for s in summaries], ["today", "yesterday", "week", "month"])
        self.assertEqual([s["label"] for s in summaries], ["今天", "昨天", "7 天", "30 天"])
        self.assertEqual(client.calls, [sub2api_usage.period_range(p) for p in sub2api_usage.ORDINARY_SUMMARY_PERIODS])

    def test_print_usage_summaries_shows_all_windows_on_one_page(self):
        summaries = [
            {
                "period": "today",
                "label": "今天",
                "start": "2026-06-17",
                "end": "2026-06-17",
                "stats": {
                    "total_requests": 12,
                    "total_tokens": 3456,
                    "total_input_tokens": 1000,
                    "total_output_tokens": 2000,
                    "total_cache_tokens": 456,
                    "total_cost": 0.1234,
                    "total_actual_cost": 0.0567,
                    "average_duration_ms": 1500,
                },
            },
            {
                "period": "yesterday",
                "label": "昨天",
                "start": "2026-06-16",
                "end": "2026-06-16",
                "stats": {"total_requests": 0, "total_tokens": 0, "total_cost": 0},
            },
            {
                "period": "week",
                "label": "7 天",
                "start": "2026-06-11",
                "end": "2026-06-17",
                "stats": {"total_requests": 40, "total_tokens": 5000, "total_cost": 1.25},
            },
            {
                "period": "month",
                "label": "30 天",
                "start": "2026-05-19",
                "end": "2026-06-17",
                "stats": {"total_requests": 90, "total_tokens": 9000, "total_cost": 3.5},
            },
        ]
        out = StringIO()

        with redirect_stdout(out):
            sub2api_usage._print_usage_summaries(summaries, "Asia/Shanghai")

        rendered = out.getvalue()
        for label in ("今天", "昨天", "7 天", "30 天"):
            self.assertIn(label, rendered)
        self.assertIn("请求", rendered)
        self.assertIn("Token", rendered)
        self.assertIn("成本", rendered)
        self.assertNotIn("明细", rendered)

    def test_ordinary_tui_does_not_render_request_list_or_paging(self):
        source = inspect.getsource(sub2api_usage.run_tui)

        self.assertNotIn("DataTable", source)
        self.assertNotIn("Tabs", source)
        self.assertNotIn("self.client.list", source)
        self.assertNotIn("action_next_page", source)
        self.assertNotIn("action_prev_page", source)
