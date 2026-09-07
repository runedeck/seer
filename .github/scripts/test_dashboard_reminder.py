"""Exercise the dashboard's actual jq filters with local comment pages."""

import json
import re
import subprocess
import unittest
from pathlib import Path

SOURCE = Path(__file__).with_name("dashboard.sh")
STAMP = "2026-09-01T12:00:00Z"


def comment(number, *, author="runewright[bot]", body="Awaiting the owner review"):
    return {"id": number, "updated_at": STAMP, "user": {"login": author}, "body": body}


class ReminderSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = SOURCE.read_text(encoding="utf-8")
        selection = source.split("bump=$(gh api", 1)[1].split("bump_id=", 1)[0]
        cls.page_filter = re.search(r'--jq "((?:\\.|[^"\\])*)"', selection)[1]
        cls.page_filter = cls.page_filter.replace('\\"', '"')
        for variable in ("BOT_LOGIN", "BUMP_PREFIX"):
            value = re.search(rf'^{variable}="([^"]*)"$', source, re.MULTILINE)[1]
            cls.page_filter = cls.page_filter.replace(f"${variable}", value)
        cls.aggregate_filter = re.search(r"jq -s -r '([^']*)'", selection)[1]

    def select(self, *pages):
        # gh --jq filters each page separately. Only local jq runs here.
        filtered = []
        for page in pages:
            result = subprocess.run(
                ["jq", "-c", self.page_filter],
                input=json.dumps(page),
                text=True,
                capture_output=True,
                check=True,
            )
            filtered.append(result.stdout)
        return subprocess.run(
            ["jq", "-s", "-r", self.aggregate_filter],
            input="".join(filtered),
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    def test_no_comments_produces_empty_selection(self):
        self.assertEqual(self.select([]), "")

    def test_existing_reminder_selects_id_and_timestamp(self):
        self.assertEqual(self.select([comment(17)]), f"17 {STAMP}")

    def test_irrelevant_comments_produce_empty_selection(self):
        self.assertEqual(
            self.select(
                [
                    comment(1, author="another-bot[bot]"),
                    comment(2, body="Unrelated status"),
                ]
            ),
            "",
        )

    def test_irrelevant_comments_do_not_replace_existing_reminder(self):
        self.assertEqual(
            self.select(
                [
                    comment(17),
                    comment(18, author="another-bot[bot]"),
                    comment(19, body="Unrelated status"),
                ]
            ),
            f"17 {STAMP}",
        )

    def test_later_empty_pages_preserve_an_earlier_reminder(self):
        self.assertEqual(
            self.select([comment(17)], [comment(18, body="Other")], []), f"17 {STAMP}"
        )

    def test_multiple_pages_return_only_the_last_reminder(self):
        self.assertEqual(
            self.select([], [comment(17)], [], [comment(28)], []), f"28 {STAMP}"
        )

    def test_all_pages_without_matches_produce_empty_selection(self):
        self.assertEqual(self.select([], [comment(1, body="Other")], []), "")


if __name__ == "__main__":
    unittest.main()
