import unittest
from contextlib import redirect_stdout
from io import StringIO

from sub2api_usage import _account_5h_window, _account_seven_day_window, _print_admin_accounts


class AdminAccountWindowTests(unittest.TestCase):
    def test_five_hour_window_shows_usage_progress_deadline(self):
        account = {"window_cost_limit": 10}
        usage = {
            "five_hour": {
                "utilization": 25,
                "window_stats": {
                    "cost": 2.5,
                    "end_time": "2026-06-02T18:30:00+08:00",
                },
            }
        }

        rendered = _account_5h_window(account, usage)

        self.assertEqual(rendered, "$2.5000 / $10.0000 (25%) · 至 06-02 18:30")

    def test_five_hour_window_falls_back_to_account_deadline(self):
        account = {
            "current_window_cost": 1,
            "window_cost_limit": 5,
            "session_window_end": "2026-06-02T20:00:00+08:00",
        }

        rendered = _account_5h_window(account)

        self.assertEqual(rendered, "$1.0000 / $5.0000 (20%) · 至 06-02 20:00")

    def test_seven_day_window_shows_usage_progress_deadline(self):
        account = {}
        usage = {
            "seven_day": {
                "utilization": 50,
                "window_stats": {
                    "cost": 20,
                    "window_end_at": "2026-06-09T00:00:00+08:00",
                },
            }
        }

        rendered = _account_seven_day_window(account, usage)

        self.assertEqual(rendered, "$20.0000 / $40.0000 (50%) · 至 06-09 00:00")

    def test_window_without_deadline_stays_compact(self):
        account = {"current_window_cost": 1, "window_cost_limit": 5}

        rendered = _account_5h_window(account)

        self.assertEqual(rendered, "$1.0000 / $5.0000 (20%)")

    def test_accounts_print_does_not_truncate_window_deadline(self):
        account = {
            "id": 1,
            "name": "claude",
            "platform": "anthropic",
            "type": "oauth",
            "status": "active",
            "window_cost_limit": 10,
        }
        usage = {
            1: {
                "five_hour": {
                    "utilization": 25,
                    "window_stats": {
                        "cost": 2.5,
                        "end_time": "2026-06-02T18:30:00+08:00",
                    },
                },
                "seven_day": {
                    "utilization": 50,
                    "window_stats": {
                        "cost": 20,
                        "window_end_at": "2026-06-09T00:00:00+08:00",
                    },
                },
            }
        }
        out = StringIO()

        with redirect_stdout(out):
            _print_admin_accounts([account], {}, usage)

        rendered = out.getvalue()
        self.assertIn("至 06-02 18:30", rendered)
        self.assertIn("至 06-09 00:00", rendered)


if __name__ == "__main__":
    unittest.main()
