import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from doge_cogs.alignment import (  # replace with actual module filename
    AlignmentChart,
    load_yaml_file,
    parse_alignment_chart,
    remove_user_alignment,
    save_yaml_file,
    serialize_alignment_chart,
    set_user_alignment,
)


class TestAlignmentChart(unittest.TestCase):
    def test_parse_empty_yaml(self):
        buf = BytesIO()
        chart = parse_alignment_chart(buf)
        self.assertEqual(chart, {"users": {}})

    def test_set_user_alignment(self):
        chart: AlignmentChart = {"users": {}}
        updated = set_user_alignment(
            chart,
            user_id="123",
            alignment="Lawful Good",
            display_name="Tester",
            avatar_url=None,
        )
        self.assertIn("123", updated["users"])
        self.assertEqual(updated["users"]["123"]["alignment"], "Lawful Good")
        self.assertEqual(updated["users"]["123"]["display_name"], "Tester")
        self.assertEqual(chart, {"users": {}})  # original unchanged

    def test_remove_user_alignment(self):
        chart: AlignmentChart = {
            "users": {
                "123": {
                    "alignment": "Lawful Good",
                    "display_name": "Tester",
                    "avatar_url": None,
                }
            }
        }
        updated = remove_user_alignment(chart, "123")
        self.assertNotIn("123", updated["users"])
        self.assertIn("123", chart["users"])  # original unchanged

    def test_serialization_round_trip(self):
        chart: AlignmentChart = {"users": {}}
        buf = serialize_alignment_chart(chart)
        parsed = parse_alignment_chart(buf)
        self.assertEqual(chart, parsed)

    def test_load_and_save_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.yaml"

            # Save initial
            chart: AlignmentChart = {"users": {}}
            buf = serialize_alignment_chart(chart)
            save_yaml_file(path, buf)
            self.assertTrue(path.exists())

            # Load again
            loaded_buf = load_yaml_file(path)
            parsed = parse_alignment_chart(loaded_buf)
            self.assertEqual(parsed, {"users": {}})


def main():
    unittest.main()


if __name__ == "__main__":
    unittest.main()
