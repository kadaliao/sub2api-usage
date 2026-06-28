import unittest
from pathlib import Path

from rich.text import Text

from sub2api_usage import (
    _admin_search_effective_query,
    _admin_search_allows_action,
    _admin_search_status_line,
    _admin_search_suffix,
    _apply_admin_search_key,
    _handle_admin_search_key,
    _highlight_search_cells,
    _highlight_search_text,
    _row_matches_search,
)


class AdminSearchTests(unittest.TestCase):
    def test_row_search_matches_any_visible_cell_case_insensitively(self):
        self.assertTrue(_row_matches_search(["Alice@Example.com", "$12.34"], "example"))
        self.assertTrue(_row_matches_search(["Alice@Example.com", "$12.34"], "12.34"))
        self.assertFalse(_row_matches_search(["Alice@Example.com", "$12.34"], "missing"))

    def test_empty_search_matches_all_rows(self):
        self.assertTrue(_row_matches_search(["Alice@Example.com"], ""))
        self.assertTrue(_row_matches_search(["Alice@Example.com"], "   "))

    def test_highlight_search_text_keeps_plain_text_and_marks_match(self):
        rendered = _highlight_search_text(Text("Alice@Example.com", style="cyan"), "example")

        self.assertEqual(rendered.plain, "Alice@Example.com")
        self.assertTrue(any("black on yellow" in str(span.style) for span in rendered.spans))

    def test_highlight_search_text_treats_query_as_literal_text(self):
        rendered = _highlight_search_text(Text("alice+a.b@example.com"), "+a.b")

        self.assertEqual(rendered.plain, "alice+a.b@example.com")
        self.assertEqual(
            [rendered.plain[span.start:span.end] for span in rendered.spans if "black on yellow" in str(span.style)],
            ["+a.b"],
        )

    def test_highlight_search_cells_keeps_unmatched_cells_visible(self):
        rendered = _highlight_search_cells(
            [Text("Alice@Example.com"), Text("Engineering"), Text("active")],
            "alice",
        )

        self.assertEqual([cell.plain for cell in rendered], ["Alice@Example.com", "Engineering", "active"])
        self.assertTrue(any("black on yellow" in str(span.style) for span in rendered[0].spans))
        self.assertFalse(rendered[1].spans)
        self.assertFalse(rendered[2].spans)

    def test_highlight_search_cells_can_mark_entire_matching_row(self):
        rendered = _highlight_search_cells(
            [Text("Alice@Example.com"), Text("Engineering"), Text("active")],
            "alice",
            full_row=True,
        )

        self.assertEqual([cell.plain for cell in rendered], ["Alice@Example.com", "Engineering", "active"])
        for cell in rendered:
            self.assertTrue(
                any("black on yellow" in str(span.style) for span in cell.spans),
                f"{cell.plain!r} was not row-highlighted",
            )

    def test_admin_search_suffix_shows_vim_style_active_query(self):
        rendered = _admin_search_suffix("alice@example.com", 2, active=True)

        self.assertIn("[reverse]/alice@example.com[/]", rendered)
        self.assertIn("匹配 2 条", rendered)

    def test_admin_search_suffix_shows_inactive_query_without_input_prompt(self):
        rendered = _admin_search_suffix("alice@example.com", 2, active=False)

        self.assertIn("搜索 [b yellow]alice@example.com[/]", rendered)
        self.assertNotIn("[reverse]/alice@example.com[/]", rendered)

    def test_admin_search_status_line_starts_with_search_prompt(self):
        rendered = _admin_search_status_line("alice@example.com", 2)

        self.assertTrue(rendered.startswith("[reverse]/alice@example.com[/]"))
        self.assertIn("匹配 2 条", rendered)

    def test_handle_admin_search_key_appends_characters(self):
        active, query, action = _handle_admin_search_key(True, "ali", "c", "c")

        self.assertTrue(active)
        self.assertEqual(query, "alic")
        self.assertEqual(action, "changed")

    def test_handle_admin_search_key_enter_commits_and_clears_query(self):
        self.assertEqual(_handle_admin_search_key(False, "", "/", "/"), (True, "", "started"))
        self.assertEqual(_handle_admin_search_key(True, "alice", "enter", None), (False, "", "committed"))

    def test_apply_admin_search_key_commits_filter_but_clears_input(self):
        active, input_query, applied_query, action = _apply_admin_search_key(
            True,
            "alice",
            "",
            "enter",
            None,
        )

        self.assertFalse(active)
        self.assertEqual(input_query, "")
        self.assertEqual(applied_query, "alice")
        self.assertEqual(action, "committed")
        self.assertEqual(_admin_search_effective_query(active, input_query, applied_query), "alice")

    def test_apply_admin_search_escape_clears_committed_filter(self):
        self.assertEqual(
            _apply_admin_search_key(False, "", "alice", "escape", None),
            (False, "", "", "cleared"),
        )

    def test_handle_admin_search_key_escape_clears_query(self):
        self.assertEqual(_handle_admin_search_key(True, "alice", "escape", None), (False, "", "cleared"))
        self.assertEqual(_handle_admin_search_key(False, "alice", "escape", None), (False, "", "cleared"))

    def test_admin_search_binding_is_visible_in_footer(self):
        source = Path(__file__).resolve().parents[1].joinpath("sub2api_usage.py").read_text()

        self.assertIn('Binding("/", "start_search", "搜索")', source)
        self.assertNotIn('Binding("/", "start_search", "搜索", show=False)', source)

    def test_admin_search_mode_blocks_normal_tui_actions(self):
        blocked = [
            "start_search",
            "quit",
            "refresh",
            "set_view",
            "set_period",
            "sort_by",
            "toggle_sub_status",
            "cycle_sort",
        ]

        for action in blocked:
            with self.subTest(action=action):
                self.assertFalse(_admin_search_allows_action(True, action))
                self.assertTrue(_admin_search_allows_action(False, action))

    def test_admin_search_mode_replaces_footer_with_status_line(self):
        source = Path(__file__).resolve().parents[1].joinpath("sub2api_usage.py").read_text()

        self.assertIn("def _sync_search_ui(self) -> None:", source)
        self.assertIn("self.query_one(Footer).display = not self.searching", source)

    def test_admin_search_mode_removes_table_focus_for_enter_key(self):
        source = Path(__file__).resolve().parents[1].joinpath("sub2api_usage.py").read_text()

        self.assertIn("self.set_focus(None)", source)

    def test_admin_search_commit_rerenders_view_to_clear_highlights(self):
        source = Path(__file__).resolve().parents[1].joinpath("sub2api_usage.py").read_text()

        self.assertIn('if action in {"changed", "committed", "cleared"}:', source)

    def test_admin_tui_search_does_not_render_input_widget(self):
        source = Path(__file__).resolve().parents[1].joinpath("sub2api_usage.py").read_text()

        self.assertNotIn('id="search"', source)
        self.assertNotIn("on_input_changed", source)
        self.assertNotIn("on_input_submitted", source)
        self.assertNotIn("search-hidden", source)


if __name__ == "__main__":
    unittest.main()
