"""Test the event routing for the Runeseer workflow caller."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).parent.parent / "workflows" / "review-entry-correctness.yaml"
PAID_WORKFLOW = "uses: runedeck/seer/.github/workflows/review-correctness.yaml@main"


def job_block(source: str, name: str) -> str:
    marker = f"    {name}:\n"
    start = source.index(marker)
    following = source[start + len(marker) :]
    next_job = re.search(r"(?m)^    [a-z][a-z0-9-]*:\n", following)
    end = start + len(marker) + (next_job.start() if next_job else len(following))
    return source[start:end]


class ReviewEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_synchronize_only_cancels_and_refreshes_the_mirror(self):
        self.assertIn(
            "types: [labeled, unlabeled, synchronize, ready_for_review, edited]",
            self.source,
        )
        self.assertRegex(
            self.source,
            r"cancel-in-progress: .*github\.event\.action == 'synchronize'",
        )
        self.assertEqual(self.source.count(PAID_WORKFLOW), 1)

        paid_job = job_block(self.source, "review")
        self.assertIn(PAID_WORKFLOW, paid_job)
        self.assertIn("github.event.action == ''labeled''", paid_job)
        self.assertIn("github.event.action == ''ready_for_review''", paid_job)
        self.assertNotIn("synchronize", paid_job)

        mirror_job = job_block(self.source, "review-context")
        self.assertIn("if: always()", mirror_job)


if __name__ == "__main__":
    unittest.main()
