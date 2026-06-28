import asyncio
import sys
import unittest
from contextlib import redirect_stderr
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import sub2api_usage


class ProfileCommandTests(unittest.TestCase):
    def test_profile_flag_is_available_on_root_and_admin_commands(self):
        parser = sub2api_usage.build_parser()

        ordinary_short = parser.parse_args(["-p", "work"])
        ordinary_long = parser.parse_args(["--profile", "work"])
        admin_short = parser.parse_args(["admin", "-p", "root"])
        admin_long = parser.parse_args(["admin", "--profile", "root", "print"])

        self.assertEqual(ordinary_short.profile, "work")
        self.assertEqual(ordinary_long.profile, "work")
        self.assertEqual(admin_short.profile, "root")
        self.assertEqual(admin_long.profile, "root")
        self.assertEqual(admin_long.admin_cmd, "print")

    def test_ordinary_profile_flag_resolves_ordinary_namespace(self):
        cfg = {"profiles": {"work": {"email": "user@example.com"}}}
        resolved = []

        def fake_resolve(cfg_arg, name=None, namespace="profiles"):
            resolved.append((cfg_arg, name, namespace))
            return "work", {"base_url": "https://example.com", "email": "user@example.com", "password": "pw", "timezone": "UTC"}

        with (
            patch.object(sys, "argv", ["sub2api-usage", "-p", "work", "print"]),
            patch.object(sub2api_usage, "load_config", return_value=cfg),
            patch.object(sub2api_usage, "resolve_profile", side_effect=fake_resolve),
            patch.object(sub2api_usage, "cmd_print", side_effect=lambda *_: None),
        ):
            code = sub2api_usage._main()

        self.assertEqual(code, 0)
        self.assertEqual(resolved, [(cfg, "work", "profiles")])

    def test_admin_profile_flag_resolves_admin_namespace(self):
        cfg = {
            "admin_profiles": {
                "root": {"base_url": "https://example.com", "email": "admin@example.com", "password": "pw", "timezone": "UTC"}
            },
        }
        resolved = []

        def fake_resolve(cfg_arg, name=None, namespace="profiles"):
            resolved.append((cfg_arg, name, namespace))
            return "root", cfg["admin_profiles"]["root"]

        with (
            patch.object(sys, "argv", ["sub2api-usage", "admin", "-p", "root", "print"]),
            patch.object(sub2api_usage, "load_config", return_value=cfg),
            patch.object(sub2api_usage, "resolve_profile", side_effect=fake_resolve),
            patch.object(sub2api_usage, "cmd_admin_print", side_effect=lambda *_args, **_kwargs: None),
            redirect_stderr(StringIO()),
        ):
            code = sub2api_usage._main()

        self.assertEqual(code, 0)
        self.assertEqual(resolved, [(cfg, "root", "admin_profiles")])

    def test_profiles_add_is_parsed_for_ordinary_and_admin_namespaces(self):
        parser = sub2api_usage.build_parser()

        ordinary = parser.parse_args(["profiles", "add", "work"])
        admin = parser.parse_args(["admin", "profiles", "add", "root"])

        self.assertEqual(ordinary.action, "add")
        self.assertEqual(ordinary.name, "work")
        self.assertEqual(admin.admin_action, "add")
        self.assertEqual(admin.name, "root")

    def test_profiles_add_runs_setup_even_when_no_profile_exists(self):
        with (
            patch.object(sys, "argv", ["sub2api-usage", "profiles", "add", "work"]),
            patch.object(sub2api_usage, "load_config", return_value=None),
            patch.object(sub2api_usage.asyncio, "run", return_value={}) as run,
        ):
            code = sub2api_usage._main()

        self.assertEqual(code, 0)
        run.assert_called_once()
        coro = run.call_args.args[0]
        self.assertEqual(coro.cr_frame.f_locals["cfg"], {})
        self.assertEqual(coro.cr_frame.f_locals["name"], "work")
        self.assertEqual(coro.cr_frame.f_locals["namespace"], "profiles")
        coro.close()

    def test_admin_profiles_add_runs_admin_setup_even_when_no_admin_profile_exists(self):
        cfg = {"profiles": {"default": {"email": "user@example.com"}}}

        with (
            patch.object(sys, "argv", ["sub2api-usage", "admin", "profiles", "add", "root"]),
            patch.object(sub2api_usage, "load_config", return_value=cfg),
            patch.object(sub2api_usage.asyncio, "run", return_value={}) as run,
        ):
            code = sub2api_usage._main()

        self.assertEqual(code, 0)
        run.assert_called_once()
        coro = run.call_args.args[0]
        self.assertEqual(coro.cr_frame.f_locals["cfg"], cfg)
        self.assertEqual(coro.cr_frame.f_locals["name"], "root")
        self.assertEqual(coro.cr_frame.f_locals["namespace"], "admin_profiles")
        coro.close()

    def test_setup_with_existing_profiles_selects_existing_profile_before_editing(self):
        cfg = {
            "default": "default",
            "profiles": {
                "default": {"base_url": "https://old", "email": "old@example.com", "password": "pw", "timezone": "UTC"},
                "work": {"base_url": "https://work", "email": "work@example.com", "password": "pw", "timezone": "UTC"},
            },
        }
        calls = []

        async def fake_edit(cfg_arg, name, namespace):
            calls.append((cfg_arg, name, namespace))
            return cfg_arg

        with (
            patch.object(sub2api_usage, "_edit_profile", side_effect=fake_edit),
            patch("builtins.input", return_value="2"),
            redirect_stdout(StringIO()) as out,
        ):
            result = asyncio.run(sub2api_usage.run_setup(cfg))

        self.assertIs(result, cfg)
        self.assertEqual(calls, [(cfg, "work", "profiles")])
        self.assertIn("现有 profile", out.getvalue())
        self.assertIn("2. work", out.getvalue())

    def test_setup_with_name_still_selects_when_profiles_already_exist(self):
        cfg = {
            "default": "default",
            "profiles": {
                "default": {"base_url": "https://old", "email": "old@example.com", "password": "pw", "timezone": "UTC"},
                "work": {"base_url": "https://work", "email": "work@example.com", "password": "pw", "timezone": "UTC"},
            },
        }
        calls = []

        async def fake_edit(cfg_arg, name, namespace):
            calls.append((cfg_arg, name, namespace))
            return cfg_arg

        with (
            patch.object(sub2api_usage, "_edit_profile", side_effect=fake_edit),
            patch("builtins.input", return_value="1"),
            redirect_stdout(StringIO()) as out,
        ):
            result = asyncio.run(sub2api_usage.run_setup(cfg, name="newname"))

        self.assertIs(result, cfg)
        self.assertEqual(calls, [(cfg, "default", "profiles")])
        self.assertIn("现有 profile", out.getvalue())

    def test_setup_with_existing_profiles_can_enter_add_flow(self):
        cfg = {
            "admin_profiles": {
                "admin": {"base_url": "https://old", "email": "admin@example.com", "password": "pw", "timezone": "UTC"}
            },
            "admin_default": "admin",
        }
        calls = []

        async def fake_edit(cfg_arg, name, namespace):
            calls.append((cfg_arg, name, namespace))
            return cfg_arg

        with (
            patch.object(sub2api_usage, "_edit_profile", side_effect=fake_edit),
            patch("builtins.input", side_effect=["a", "root"]),
            redirect_stdout(StringIO()) as out,
        ):
            result = asyncio.run(sub2api_usage.run_setup(cfg, namespace="admin_profiles"))

        self.assertIs(result, cfg)
        self.assertEqual(calls, [(cfg, "root", "admin_profiles")])
        self.assertIn("a. 新增 profile", out.getvalue())


if __name__ == "__main__":
    unittest.main()
