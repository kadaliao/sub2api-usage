import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

import sub2api_usage


class CliDefaultTests(unittest.TestCase):
    def test_default_period_is_today(self):
        parser = sub2api_usage.build_parser()

        args = parser.parse_args(["print"])

        self.assertEqual(args.period, "today")

    def test_missing_config_in_non_interactive_mode_returns_clear_error(self):
        err = StringIO()

        with (
            patch.object(sys, "argv", ["sub2api-usage"]),
            patch.object(sys.stdin, "isatty", return_value=False),
            patch.object(sys.stdout, "isatty", return_value=False),
            patch.object(sub2api_usage, "load_config", return_value=None),
            redirect_stderr(err),
        ):
            code = sub2api_usage._main()

        self.assertEqual(code, 1)
        self.assertIn("未检测到配置", err.getvalue())
        self.assertIn("sub2api-usage setup", err.getvalue())


if __name__ == "__main__":
    unittest.main()
