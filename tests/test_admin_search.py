import unittest

from rich.text import Text

from sub2api_usage import _highlight_search_text, _row_matches_search


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


if __name__ == "__main__":
    unittest.main()
