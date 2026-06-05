import unittest

from rich.text import Text

from sub2api_usage import _admin_search_suffix, _highlight_search_cells, _highlight_search_text, _row_matches_search


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

    def test_admin_search_suffix_shows_current_query(self):
        rendered = _admin_search_suffix("alice@example.com", 2)

        self.assertIn("搜索: [b yellow]alice@example.com[/]", rendered)
        self.assertIn("匹配 2 条", rendered)


if __name__ == "__main__":
    unittest.main()
