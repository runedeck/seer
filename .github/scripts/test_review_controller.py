"""Execute the controller step's shell against local GitHub API fixtures."""

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
WORKFLOW = (SCRIPTS.parent / "workflows" / "review-correctness.yaml").read_text(encoding="utf-8")
LANES = (SCRIPTS.parent / "lanes.json").read_text(encoding="utf-8")
HEAD = "0123456789abcdef0123456789abcdef01234567"
BASE = "abcdef0123456789abcdef0123456789abcdef01"

# The fixture applies gh --jq, then the workflow executes its real selectors.
MOCK_API = r"""
gh() {
    printf '%s\n' "$*" >> "$TRACE"
    local request="$*" query="" endpoint="" response=""
    while [ "$#" -gt 0 ]; do
        if [ "$1" = "--jq" ]; then query=$2; shift; fi
        case "$1" in
            repos/*|search/*|graphql) endpoint=$1 ;;
        esac
        shift
    done
    case "$request" in
        *'-X POST '*'/comments'*)
            printf '%s\n' "$request" >> "$POSTED"
            response='{}' ;;
        *'/contents/.github/lanes.json'*)
            printf '%s\n' "$LANE_TABLE"; return 0 ;;
        'api graphql '*)
            response=$(jq -n --argjson nodes "$MOCK_THREADS" '{data:{repository:{pullRequest:{reviewThreads:{nodes:$nodes}}}}}') ;;
        *'/check-runs?'*)
            response=$(jq -n --argjson runs "$MOCK_RUNS" '{check_runs:$runs}') ;;
        *'/labels?'*)
            response=$MOCK_LABELS ;;
        *'/actions/artifacts?'*)
            response='{"artifacts":[]}' ;;
        *'/issues/7/comments?'*)
            response=$MOCK_COMMENTS ;;
        *'/pulls/7 '*|*'/pulls/7')
            response=$(jq -n --arg head "$MOCK_HEAD" --arg body "$MOCK_BODY" \
                '{head:{sha:$head}, body:$body, comments:3, review_comments:2}') ;;
        *' search/issues '*)
            response='{"items":[]}' ;;
        *) printf 'Unexpected API request: %s\n' "$request" >&2; return 99 ;;
    esac
    if [ -n "$query" ]; then jq -r "$query" <<<"$response"; else printf '%s\n' "$response"; fi
}
"""


def thread(identifier, login, comment_id):
    return {
        "id": identifier,
        "path": "src/lib.rs",
        "line": 3,
        "originalLine": 3,
        "comments": {"nodes": [{"databaseId": comment_id, "url": f"https://x/r{comment_id}", "author": {"login": login}}]},
    }


class ControllerStepTests(unittest.TestCase):
    def run_controller(self, *, files=("src/lib.rs",), threads=(), runs=(), labels=(),
                       earlier_rounds=0, forced=False, skipped=False, live_head=HEAD,
                       body="Change: docs/changes/review-loop"):
        env_block = WORKFLOW.split("\nenv:\n", 1)[1].split("\njobs:", 1)[0]
        env = os.environ.copy()
        for line in env_block.splitlines():
            key, _, value = line.strip().partition(": ")
            if key and key.isupper() and not value.startswith("${{"):
                env[key] = value.strip("'\"")
        body_text = WORKFLOW.split("- id: controller", 1)[1].split("run: |\n", 1)[1]
        body_text = body_text.split("\n            - id: lanes", 1)[0]
        body_text = textwrap.dedent(body_text)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pull-request-files.txt").write_text("\n".join(files) + "\n", encoding="utf-8")
            for name in ("trace", "posted", "step-output", "step-summary"):
                (root / name).write_text("", encoding="utf-8")
            env.update(
                REPO="runedeck/example", PR="7", HEAD_SHA=HEAD, BASE_SHA=BASE,
                DEFAULT_BRANCH="main", PREV_SHA="", EARLIER_ROUNDS=str(earlier_rounds),
                SCOPE_SKIPPED=str(skipped).lower(), FORCED=str(forced).lower(),
                GH_TOKEN="workflow-fixture", REVIEWER_LOGIN="runeseer[bot]",
                RUNESEER_FORMATTER=str(SCRIPTS / "runeseer_summary.py"),
                LANE_VOLUME_LIMIT="40", RUNNER_TEMP=str(root),
                GITHUB_OUTPUT=str(root / "step-output"), GITHUB_STEP_SUMMARY=str(root / "step-summary"),
                TRACE=str(root / "trace"), POSTED=str(root / "posted"),
                LANE_TABLE=LANES, MOCK_THREADS=json.dumps(list(threads)),
                MOCK_RUNS=json.dumps(list(runs)), MOCK_LABELS=json.dumps([{"name": label} for label in labels]),
                MOCK_COMMENTS="[]", MOCK_HEAD=live_head, MOCK_BODY=body,
            )
            result = subprocess.run(
                ["bash", "-c", MOCK_API + body_text], cwd=root, env=env,
                capture_output=True, text=True, check=False,
            )
            outputs = dict(
                line.split("=", 1) for line in (root / "step-output").read_text(encoding="utf-8").splitlines()
            )
            ledger_path = root / "runeseer-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else None
            posted = (root / "posted").read_text(encoding="utf-8")
            summary = (root / "step-summary").read_text(encoding="utf-8")
        return result, outputs, ledger, posted, summary

    def test_prose_only_range_stands_down_and_posts_one_owner_line(self):
        result, outputs, ledger, posted, summary = self.run_controller(
            files=("README.md", "docs/notes/plan.md"),
            threads=[thread("PRRT_1", "cursor", 11)],
            runs=[{"app": {"slug": "cursor"}, "status": "completed", "conclusion": "success", "completed_at": "2026-09-19T00:00:00Z", "output": {"title": "", "summary": ""}}],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs["stand_down"], "true")
        self.assertTrue(outputs["coverage"].startswith("free lanes only: the diff since the last verdict"))
        self.assertEqual(ledger["lanes"]["runeseer"], "skipped")
        self.assertEqual(ledger["lanes"]["cursor"], "completed")
        self.assertEqual(ledger["generation"], 1)
        self.assertEqual(ledger["coverage"], outputs["coverage"])
        self.assertEqual(posted.count("-X POST"), 1)
        self.assertIn("review/correctness stood down on `01234567`", posted)
        self.assertIn("This green check records the coverage state, not a clean verdict.", summary)

    def test_instruction_path_in_the_range_admits_the_paid_lane(self):
        result, outputs, ledger, posted, _ = self.run_controller(files=("README.md", "nested/AGENTS.md"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs["stand_down"], "false")
        self.assertEqual(outputs["coverage"], "paid")
        self.assertEqual(ledger["lanes"]["runeseer"], "ineligible")
        self.assertEqual(posted, "")

    def test_fourth_round_is_refused_even_when_the_owner_forces_it(self):
        result, outputs, ledger, posted, _ = self.run_controller(earlier_rounds=3, forced=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs["stand_down"], "true")
        self.assertIn("has spent 3 paid rounds", outputs["coverage"])
        self.assertEqual(ledger["paid_rounds"], 3)
        self.assertIn("The pull request waits for the owner.", posted)

    def test_skip_label_writes_the_ledger_with_the_lane_skipped(self):
        result, outputs, ledger, posted, _ = self.run_controller(skipped=True, labels=("skip:runeseer",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs["stand_down"], "true")
        self.assertEqual(ledger["lanes"]["runeseer"], "skipped")
        self.assertIn("skip:runeseer", ledger["coverage"])
        self.assertEqual(posted, "")

    def test_unknown_login_enters_the_ledger_with_no_lane(self):
        result, outputs, ledger, _, _ = self.run_controller(threads=[thread("PRRT_2", "stranger", 22)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs["stand_down"], "false")
        self.assertEqual(ledger["threads"][0]["lane"], None)
        self.assertEqual(ledger["threads"][0]["login"], "stranger")

    def test_moved_head_posts_no_notice(self):
        result, outputs, ledger, posted, _ = self.run_controller(files=("README.md",), live_head="f" * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs["stand_down"], "true")
        self.assertIsNotNone(ledger)
        self.assertEqual(posted, "")


if __name__ == "__main__":
    unittest.main()
