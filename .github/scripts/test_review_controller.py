"""Execute the controller workflow's shell blocks against local GitHub API fixtures.

Each test runs one `run:` block of the correctness lane or its entry
mirror under a bash `gh` mock, so a guard order or a shell error fails
here rather than on a live pull request.
"""

import io
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
WORKFLOW = (SCRIPTS.parent / "workflows" / "review-correctness.yaml").read_text(encoding="utf-8")
ENTRY = (SCRIPTS.parent / "workflows" / "review-entry-correctness.yaml").read_text(encoding="utf-8")
LANES = (SCRIPTS.parent / "lanes.json").read_text(encoding="utf-8")
HEAD = "0123456789abcdef0123456789abcdef01234567"
BASE = "abcdef0123456789abcdef0123456789abcdef01"
BODY = "Change: docs/changes/review-loop\n\n## Release Notes\n- N/A"

# The fixture applies gh --jq, then the workflow executes its real selectors.
# Every mutation is appended to $POSTED and answered with a benign body.
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
        *'-X POST '*|*'-X PUT '*|*'-X DELETE '*|*'-X PATCH '*)
            printf '%s\n' "$request" >> "$POSTED"
            response='{"id": 900, "state": "APPROVED", "commit_id": "'"$HEAD_SHA"'"}' ;;
        *'mutation('*)
            printf '%s\n' "$request" >> "$POSTED"
            response='{"data":{"resolveReviewThread":{"thread":{"isResolved":true}}}}' ;;
        *'/contents/.github/lanes.json'*)
            printf '%s\n' "$LANE_TABLE"; return 0 ;;
        *'/contents/.github/scripts/runeseer_summary.py'*)
            cat "$RUNESEER_FORMATTER"; return 0 ;;
        'api graphql '*)
            response=$(jq -n --argjson nodes "$MOCK_THREADS" '{data:{repository:{pullRequest:{reviewThreads:{nodes:$nodes}}}}}') ;;
        *'/check-runs?'*)
            response=$(jq -n --argjson runs "$MOCK_RUNS" '{check_runs:$runs}') ;;
        *'/labels?'*)
            response=$MOCK_LABELS ;;
        *'/actions/artifacts?'*)
            response=$(jq -n --argjson artifacts "$MOCK_ARTIFACTS" '{artifacts:$artifacts}') ;;
        *'/actions/artifacts/'*'/zip'*)
            cat "$MOCK_LEDGER_ZIP"; return 0 ;;
        *'/actions/runs/'*'/jobs?'*)
            response=$MOCK_JOBS ;;
        *'/actions/runs/'*)
            response=$(jq --arg id "${request##*/actions/runs/}" '.[$id] // {}' <<<"$MOCK_RUNS_BY_ID") ;;
        *'/issues/7/comments?'*)
            response=$MOCK_COMMENTS ;;
        *'/issues/8/comments?'*)
            response=$MOCK_SIBLING_COMMENTS ;;
        *'/pulls/7/reviews?'*)
            response=$MOCK_REVIEWS ;;
        *'/pulls/8 '*|*'/pulls/8')
            response=$(jq -n --arg body "$MOCK_SIBLING_BODY" '{body:$body}') ;;
        *'/pulls/7 '*|*'/pulls/7')
            response=$(jq -n --arg head "$MOCK_HEAD" --arg base "$MOCK_BASE" --arg body "$MOCK_BODY" \
                '{head:{sha:$head}, base:{sha:$base}, body:$body, comments:3, review_comments:2}') ;;
        *' search/issues '*)
            [ "$MOCK_SEARCH_FAILS" != "true" ] || return 1
            response=$MOCK_SEARCH ;;
        *) printf 'Unexpected API request: %s\n' "$request" >&2; return 99 ;;
    esac
    if [ -n "$query" ]; then jq -r "$query" <<<"$response"; else printf '%s\n' "$response"; fi
}
# The runner installs as root into read-only paths. The fixture installs
# as the test user into its temporary directory.
sudo() {
    local args=()
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -o|-g|-m) shift 2; continue ;;
        esac
        args+=("$1"); shift
    done
    "${args[@]}"
}
sleep() { :; }
"""


def thread(identifier, login, comment_id):
    return {
        "id": identifier,
        "path": "src/lib.rs",
        "line": 3,
        "originalLine": 3,
        "isResolved": False,
        "comments": {"nodes": [{"databaseId": comment_id, "url": f"https://x/r{comment_id}", "author": {"login": login}}]},
    }


def check_run(name, conclusion="success", status="completed", slug=None):
    return {
        "name": name, "app": {"slug": slug or name}, "status": status, "conclusion": conclusion,
        "started_at": "2026-09-19T00:00:00Z", "completed_at": "2026-09-19T00:01:00Z",
        "output": {"title": "", "summary": ""},
    }


def ledger_zip(ledger):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("runeseer-ledger.json", json.dumps(ledger))
    return buffer.getvalue()


def env_defaults(source):
    env_block = source.split("\nenv:\n", 1)[1].split("\njobs:", 1)[0]
    values = {}
    for line in env_block.splitlines():
        key, _, value = line.strip().partition(": ")
        if key and key.isupper() and not value.startswith("${{"):
            values[key] = value.strip("'\"")
    return values


def step_block(source, step, next_step):
    body = source.split(step, 1)[1].split("run: |\n", 1)[1]
    body = body.split(next_step, 1)[0]
    return textwrap.dedent(body)


class BlockRunner(unittest.TestCase):
    """Run one workflow run block in a temporary directory under the mock."""

    def run_block(self, block, *, files=("src/lib.rs",), threads=(), runs=(), labels=(), comments=(),
                  artifacts=(), runs_by_id=None, ledger=None, reviews=(), jobs=None, search=(),
                  search_fails=False, sibling_body=BODY, sibling_comments=(), live_head=HEAD,
                  live_base=BASE, body=BODY, workspace=None, env_extra=None, setup=None):
        env = os.environ.copy()
        env.update(env_defaults(WORKFLOW))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lanes_dir = root / "lanes"
            lanes_dir.mkdir()
            (lanes_dir / "lanes.json").write_text(LANES, encoding="utf-8")
            (root / "pull-request-files.txt").write_text(
                "".join(f"modified\t{name}\n" for name in files), encoding="utf-8")
            for name in ("trace", "posted", "step-output", "step-summary"):
                (root / name).write_text("", encoding="utf-8")
            (root / "mock-ledger.zip").write_bytes(ledger_zip(ledger) if ledger else b"")
            formatter = root / "runeseer_summary.py"
            formatter.write_bytes((SCRIPTS / "runeseer_summary.py").read_bytes())
            formatter.chmod(0o555)
            env.update(
                REPO="runedeck/example", PR="7", HEAD_SHA=HEAD, BASE_SHA=BASE, RUN_ID="4242",
                DEFAULT_BRANCH="main", PREV_SHA="", PAID_ROUNDS="0", SCOPE_SKIPPED="false",
                LEDGER_ONLY="false", GH_TOKEN="workflow-fixture", REVIEWER_LOGIN="runeseer[bot]",
                RUNESEER_FORMATTER=str(formatter), RUNESEER_LANES=str(lanes_dir),
                LANE_VOLUME_LIMIT="40", RUNNER_TEMP=str(root), POLL_ROUNDS="2", POLL_SECONDS="0",
                GREEN_HEAD_CHECKS="quality", GITHUB_OUTPUT=str(root / "step-output"),
                GITHUB_STEP_SUMMARY=str(root / "step-summary"), TRACE=str(root / "trace"),
                POSTED=str(root / "posted"), LANE_TABLE=LANES, MOCK_THREADS=json.dumps(list(threads)),
                MOCK_RUNS=json.dumps(list(runs)), MOCK_LABELS=json.dumps([{"name": label} for label in labels]),
                MOCK_COMMENTS=json.dumps(list(comments)), MOCK_HEAD=live_head, MOCK_BASE=live_base,
                MOCK_BODY=body, MOCK_ARTIFACTS=json.dumps(list(artifacts)),
                MOCK_RUNS_BY_ID=json.dumps(runs_by_id or {}), MOCK_LEDGER_ZIP=str(root / "mock-ledger.zip"),
                MOCK_REVIEWS=json.dumps(list(reviews)),
                MOCK_JOBS=json.dumps(jobs if jobs is not None else {"jobs": []}),
                MOCK_SEARCH=json.dumps({"items": [{"number": number} for number in search]}),
                MOCK_SEARCH_FAILS=str(search_fails).lower(), MOCK_SIBLING_BODY=sibling_body,
                MOCK_SIBLING_COMMENTS=json.dumps(list(sibling_comments)),
            )
            env.update(env_extra or {})
            cwd = root / "workspace"
            cwd.mkdir()
            for name, content in (workspace or {}).items():
                (cwd / name).write_text(content, encoding="utf-8")
            if setup is not None:
                setup(cwd, env)
            result = subprocess.run(
                ["bash", "-c", MOCK_API + block], cwd=cwd, env=env,
                capture_output=True, text=True, check=False,
            )
            outputs = {}
            for line in (root / "step-output").read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition("=")
                outputs[key] = value
            ledger_path = cwd / "runeseer-ledger.json"
            written = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else None
            verdict_path = cwd / "runeseer-verdict.json"
            verdict_exists = verdict_path.exists()
            posted = (root / "posted").read_text(encoding="utf-8")
            summary = (root / "step-summary").read_text(encoding="utf-8")
        return Run(result, outputs, written, posted, summary, verdict_exists)


class Run:
    def __init__(self, result, outputs, ledger, posted, summary, verdict_exists):
        self.result = result
        self.outputs = outputs
        self.ledger = ledger
        self.posted = posted
        self.summary = summary
        self.verdict_exists = verdict_exists
        self.stdout = result.stdout
        self.returncode = result.returncode


def trusted_artifact(run_id=77, artifact_id=5):
    artifacts = [{"id": artifact_id, "expired": False, "created_at": "2026-09-19T00:00:00Z",
                  "workflow_run": {"id": run_id}}]
    runs_by_id = {str(run_id): {"event": "pull_request_target", "pull_requests": [{"number": 7}]}}
    return artifacts, runs_by_id


def recorded_ledger(**overrides):
    ledger = {
        "schema": 1, "pull_request": 7, "reviewed_sha": HEAD, "base": BASE, "generation": 1,
        "work_item": "docs/changes/review-loop", "paid_rounds": 1,
        "body_digest": __import__("hashlib").sha256((BODY + "\n").encode()).hexdigest(),
        "lanes": {"codex": "ineligible", "cursor": "ineligible", "coderabbit": "ineligible",
                  "macroscope": "ineligible", "runeseer": "completed-no-findings"},
        "threads": [], "coverage": "paid",
        "verdict": {"sha": HEAD, "generation": 1, "round": 1, "verdict": "clean", "count": 0},
    }
    ledger.update(overrides)
    return ledger


def clean_verdict():
    return {
        "sha": HEAD, "base": BASE, "round": 1, "generation": 1, "verdict": "clean", "restart": "none",
        "findings": [], "lane_judgments": [], "count": 0, "nonfinding_issue_comment_ids": [],
        "thread_dispositions": [],
    }


CONTROLLER = step_block(WORKFLOW, "- id: controller", "\n            # Only lane bots supply finding identities.")
DISPATCH = step_block(WORKFLOW, "- id: dispatch", "\n            - id: adjudicate")
PREVALIDATE = step_block(WORKFLOW, "- id: prevalidate", "\n            # The model writes findings.")
RESOLVE = step_block(WORKFLOW, "- id: resolve", "\n            # The publisher verifies")
GATE = step_block(WORKFLOW, "- name: Verdict gates the check", "\n            # Metrics:")
MIRROR = step_block(ENTRY, "review-context:", "\nEND-OF-FILE")


class ControllerStepTests(BlockRunner):
    def controller(self, **kwargs):
        return self.run_block(CONTROLLER, runs=kwargs.pop("runs", [check_run("quality")]), **kwargs)

    def test_prose_only_range_stands_down_and_posts_one_owner_line(self):
        run = self.controller(
            files=("README.md", "docs/notes/plan.md"),
            threads=[thread("PRRT_1", "cursor", 11)],
            runs=[check_run("quality"), check_run("cursor")],
        )
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.outputs["stand_down"], "true")
        self.assertTrue(run.outputs["coverage"].startswith("free lanes only: the diff since the last verdict"))
        self.assertEqual(run.ledger["lanes"]["runeseer"], "skipped")
        self.assertEqual(run.ledger["lanes"]["cursor"], "completed")
        self.assertEqual(run.ledger["generation"], 1)
        self.assertEqual(run.ledger["coverage"], run.outputs["coverage"])
        self.assertEqual(run.posted.count("-X POST"), 1)
        self.assertIn("review/correctness stood down on `01234567`", run.posted)
        self.assertIn("This green check records the coverage state, not a clean verdict.", run.summary)

    def test_existing_notice_is_not_posted_twice(self):
        notice = "<!-- runeseer-standdown head=" + HEAD + " generation=1 -->\nreview/correctness stood down"
        run = self.controller(files=("README.md",), comments=[{"id": 1, "user": {"login": "runeseer[bot]"}, "body": notice}])
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.outputs["stand_down"], "true")
        self.assertEqual(run.posted, "")

    def test_instruction_path_in_the_range_admits_the_paid_lane(self):
        run = self.controller(files=("README.md", "nested/AGENTS.md"))
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.outputs["stand_down"], "false")
        self.assertEqual(run.outputs["coverage"], "paid")
        # The lane has not run yet: its status is recorded by the verdict.
        self.assertIsNone(run.ledger["verdict"])
        self.assertEqual(run.posted, "")

    def test_renamed_workflow_is_never_prose_only(self):
        def rename(cwd, env):
            git_env = {**env, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}

            def git(*arguments):
                return subprocess.run(["git", *arguments], cwd=cwd, env=git_env, capture_output=True, text=True, check=True).stdout.strip()

            git("init", "-q", "-b", "main")
            (cwd / ".github" / "workflows").mkdir(parents=True)
            (cwd / ".github" / "workflows" / "guard.yaml").write_text("name: guard\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-q", "-m", "base")
            env["PREV_SHA"] = git("rev-parse", "HEAD")
            (cwd / "docs").mkdir()
            # The workflow file leaves the tree under a prose name.
            (cwd / "docs" / "notes.md").write_bytes((cwd / ".github" / "workflows" / "guard.yaml").read_bytes())
            (cwd / ".github" / "workflows" / "guard.yaml").unlink()
            git("add", "-A")
            git("commit", "-q", "-m", "rename")

        run = self.controller(setup=rename)
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.outputs["stand_down"], "false")
        self.assertEqual(run.outputs["coverage"], "paid")

    def test_files_api_rename_is_never_prose_only(self):
        def rename(cwd, env):
            Path(env["RUNNER_TEMP"], "pull-request-files.txt").write_text(
                "renamed\tdocs/notes.md\nrenamed-from\t.github/workflows/guard.yaml\n", encoding="utf-8")

        run = self.controller(setup=rename)
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.outputs["coverage"], "paid")

    def test_fourth_round_is_refused_even_when_the_owner_forces_it(self):
        run = self.controller(env_extra={"PAID_ROUNDS": "3"}, labels=("review:runeseer",))
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.outputs["stand_down"], "true")
        self.assertIn("has spent 3 paid rounds", run.outputs["coverage"])
        self.assertEqual(run.ledger["paid_rounds"], 3)
        self.assertIn("The pull request waits for the owner.", run.posted)

    def test_sibling_rounds_count_against_the_work_item(self):
        marker = "<!-- runeseer-round-start sha=" + "e" * 40 + " base=" + BASE + " round=2 -->"
        run = self.controller(
            env_extra={"PAID_ROUNDS": "1"}, search=(8,),
            sibling_comments=[{"user": {"login": "runeseer[bot]"}, "body": marker}],
        )
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.ledger["paid_rounds"], 3)
        self.assertEqual(run.outputs["stand_down"], "true")
        self.assertIn("has spent 3 paid rounds", run.outputs["coverage"])
        # A sibling naming another change is not counted.
        other = self.controller(env_extra={"PAID_ROUNDS": "1"}, search=(8,), sibling_body="Change: docs/changes/review-loop-two",
                                sibling_comments=[{"user": {"login": "runeseer[bot]"}, "body": marker}])
        self.assertEqual(other.ledger["paid_rounds"], 1)
        self.assertEqual(other.outputs["stand_down"], "false")

    def test_search_failure_fails_the_step(self):
        run = self.controller(search_fails=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn("could not search the work item's other pull requests", run.stdout)
        self.assertIsNone(run.ledger)

    def test_skip_label_writes_the_ledger_with_the_lane_skipped(self):
        run = self.controller(env_extra={"SCOPE_SKIPPED": "true"}, labels=("skip:runeseer",))
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.outputs["stand_down"], "true")
        self.assertEqual(run.ledger["lanes"]["runeseer"], "skipped")
        self.assertIn("skip:runeseer", run.ledger["coverage"])
        self.assertEqual(run.posted, "")

    def test_unknown_login_enters_the_ledger_with_no_lane(self):
        run = self.controller(threads=[thread("PRRT_2", "stranger", 22)])
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.outputs["stand_down"], "false")
        self.assertEqual(run.ledger["threads"][0]["lane"], None)
        self.assertEqual(run.ledger["threads"][0]["login"], "stranger")

    def test_moved_head_posts_no_notice(self):
        run = self.controller(files=("README.md",), live_head="f" * 40)
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.outputs["stand_down"], "true")
        self.assertIsNotNone(run.ledger)
        self.assertEqual(run.posted, "")

    def test_red_or_absent_deterministic_check_stands_the_lane_down(self):
        red = self.controller(runs=[check_run("quality", "failure")])
        self.assertEqual(red.returncode, 0, red.result.stderr)
        self.assertEqual(red.outputs["stand_down"], "true")
        self.assertIn("deterministic check quality is not green", red.outputs["coverage"])
        self.assertEqual(red.ledger["lanes"]["runeseer"], "skipped")
        absent = self.controller(runs=[])
        self.assertEqual(absent.outputs["stand_down"], "true")
        self.assertIn("deterministic check quality is absent", absent.outputs["coverage"])
        # The owner's label bypasses the wait: the spend is theirs.
        forced = self.controller(runs=[check_run("quality", "failure")], labels=("review:runeseer",))
        self.assertEqual(forced.outputs["stand_down"], "false")
        self.assertEqual(forced.outputs["forced"], "true")

    def test_label_applied_in_draft_still_forces_the_round(self):
        run = self.controller(files=("README.md",), labels=("review:runeseer",))
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.outputs["stand_down"], "false")
        self.assertEqual(run.outputs["forced"], "true")

    def test_previous_ledger_is_trusted_only_from_a_base_branch_run_for_this_pull_request(self):
        artifacts, runs_by_id = trusted_artifact()
        previous = recorded_ledger()
        run = self.controller(artifacts=artifacts, runs_by_id=runs_by_id, ledger=previous)
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertIn("previous ledger: artifact 5 from run 77", run.stdout)
        # Nothing changed on this head: the verdict carries and no round starts.
        self.assertEqual(run.outputs["stand_down"], "true")
        self.assertEqual(run.ledger["verdict"], previous["verdict"])
        self.assertEqual(run.ledger["lanes"]["runeseer"], "completed-no-findings")
        self.assertEqual(run.posted, "")
        for bad_run in (
            {"event": "push", "pull_requests": [{"number": 7}]},
            {"event": "pull_request", "pull_requests": [{"number": 7}]},
            {"event": "pull_request_target", "pull_requests": [{"number": 9}]},
        ):
            with self.subTest(run=bad_run):
                untrusted = self.controller(artifacts=artifacts, runs_by_id={"77": bad_run}, ledger=previous)
                self.assertEqual(untrusted.returncode, 0, untrusted.result.stderr)
                self.assertIn("came from an untrusted run", untrusted.stdout)
                self.assertIsNone(untrusted.ledger["verdict"])
                self.assertEqual(untrusted.outputs["stand_down"], "false")
        foreign = self.controller(artifacts=artifacts, runs_by_id=runs_by_id, ledger={**previous, "pull_request": 9})
        self.assertIn("names another pull request", foreign.stdout)
        self.assertIsNone(foreign.ledger["verdict"])

    def test_body_edit_rebuilds_the_ledger_and_starts_no_round(self):
        artifacts, runs_by_id = trusted_artifact()
        run = self.controller(artifacts=artifacts, runs_by_id=runs_by_id, ledger=recorded_ledger(),
                              body=BODY + "\n\nedited", env_extra={"LEDGER_ONLY": "true"})
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.ledger["generation"], 2)
        self.assertIsNone(run.ledger["verdict"])
        self.assertEqual(run.outputs["stand_down"], "true")
        self.assertEqual(run.ledger["coverage"], "paid")
        self.assertEqual(run.posted, "")

    def test_forced_label_reruns_a_judged_head(self):
        artifacts, runs_by_id = trusted_artifact()
        run = self.controller(artifacts=artifacts, runs_by_id=runs_by_id, ledger=recorded_ledger(),
                              labels=("review:runeseer",))
        self.assertEqual(run.outputs["stand_down"], "false")

    def test_work_item_rekey_is_a_fault(self):
        artifacts, runs_by_id = trusted_artifact()
        run = self.controller(artifacts=artifacts, runs_by_id=runs_by_id, ledger=recorded_ledger(),
                              body="no change named\n\n## Release Notes\n- N/A")
        self.assertEqual(run.returncode, 1)
        self.assertIn("The work item changed from docs/changes/review-loop to pull/7", run.result.stderr)


class DispatchStepTests(BlockRunner):
    def test_round_start_is_recorded_before_the_model_call(self):
        run = self.run_block(DISPATCH, env_extra={"REVIEW_ROUND": "2", "GENERATION": "1"})
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertIn("runeseer-round-start sha=" + HEAD + " base=" + BASE + " round=2", run.posted)

    def test_moved_head_voids_the_round_before_the_model_call(self):
        run = self.run_block(DISPATCH, env_extra={"REVIEW_ROUND": "2", "GENERATION": "1"}, live_head="f" * 40)
        self.assertEqual(run.returncode, 1)
        self.assertIn("round voided by push", run.stdout)
        self.assertEqual(run.posted, "")


class PrevalidateStepTests(BlockRunner):
    def test_push_during_the_round_discards_the_verdict(self):
        run = self.run_block(
            PREVALIDATE, live_head="f" * 40, env_extra={"REVIEW_ROUND": "1", "GENERATION": "1", "RUN_URL": "https://x/run"},
            workspace={"runeseer-verdict.json": json.dumps(clean_verdict()), "runeseer-findings.rdjsonl": ""},
        )
        self.assertEqual(run.returncode, 1)
        self.assertIn("round voided by push", run.stdout)
        self.assertFalse(run.verdict_exists)


class ResolveStepTests(BlockRunner):
    def test_only_fixed_open_threads_on_the_judged_head_resolve(self):
        ledger = recorded_ledger(threads=[
            {"id": "PRRT_fixed", "lane": "codex", "comment_id": 1, "disposition": "fixed", "reason": None},
            {"id": "PRRT_rejected", "lane": "cursor", "comment_id": 2, "disposition": "rejected", "reason": "no"},
            {"id": "PRRT_owner", "lane": None, "comment_id": 3, "disposition": "owner", "reason": None},
            {"id": "PRRT_done", "lane": "codex", "comment_id": 4, "disposition": "fixed", "reason": None},
        ])
        live = [thread("PRRT_fixed", "chatgpt-codex-connector", 1), thread("PRRT_rejected", "cursor", 2),
                thread("PRRT_owner", "x", 3), {**thread("PRRT_done", "chatgpt-codex-connector", 4), "isResolved": True}]
        run = self.run_block(RESOLVE, threads=live, workspace={"runeseer-ledger.json": json.dumps(ledger)})
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertEqual(run.posted.count("mutation("), 1)
        self.assertIn("threadId=PRRT_fixed", run.posted)
        self.assertIn("resolved PRRT_fixed (fixed at " + HEAD + " generation 1)", run.stdout)

    def test_moved_head_and_other_head_ledger_resolve_nothing(self):
        ledger = recorded_ledger(threads=[{"id": "PRRT_fixed", "lane": "codex", "comment_id": 1, "disposition": "fixed", "reason": None}])
        moved = self.run_block(RESOLVE, threads=[thread("PRRT_fixed", "chatgpt-codex-connector", 1)], live_head="f" * 40,
                               workspace={"runeseer-ledger.json": json.dumps(ledger)})
        self.assertEqual(moved.returncode, 0, moved.result.stderr)
        self.assertEqual(moved.posted, "")
        other = self.run_block(RESOLVE, workspace={"runeseer-ledger.json": json.dumps({**ledger, "reviewed_sha": "f" * 40})})
        self.assertEqual(other.returncode, 1)
        self.assertEqual(other.posted, "")


class GateStepTests(BlockRunner):
    def test_stand_down_is_green_with_its_coverage_and_never_clean(self):
        run = self.run_block(GATE, env_extra={"STAND_DOWN": "true", "COVERAGE": "free lanes only: prose", "GENERATION": "1"})
        self.assertEqual(run.returncode, 0, run.result.stderr)
        self.assertIn("no paid round ran on this head; coverage: free lanes only: prose", run.stdout)

    def test_clean_bound_verdict_passes_and_an_undisposed_thread_fails(self):
        verdict = clean_verdict()
        clean = self.run_block(GATE, env_extra={"STAND_DOWN": "false", "GENERATION": "1"},
                               workspace={"runeseer-verdict.json": json.dumps(verdict)})
        self.assertEqual(clean.returncode, 0, clean.result.stderr)
        verdict["thread_dispositions"] = [{"id": "PRRT_1", "disposition": None}]
        open_thread = self.run_block(GATE, env_extra={"STAND_DOWN": "false", "GENERATION": "1"},
                                     workspace={"runeseer-verdict.json": json.dumps(verdict)})
        self.assertEqual(open_thread.returncode, 1)
        stale = self.run_block(GATE, env_extra={"STAND_DOWN": "false", "GENERATION": "2"},
                               workspace={"runeseer-verdict.json": json.dumps(clean_verdict())})
        self.assertEqual(stale.returncode, 1)


class MirrorTests(BlockRunner):
    def mirror(self, **kwargs):
        artifacts, runs_by_id = trusted_artifact()
        kwargs.setdefault("artifacts", artifacts)
        kwargs.setdefault("runs_by_id", runs_by_id)
        kwargs.setdefault("jobs", {"jobs": [{"name": "review / correctness", "conclusion": kwargs.pop("inner", "success"), "html_url": "https://x/job"}]})
        return self.run_block(MIRROR, env_extra={"REVIEW_RESULT": kwargs.pop("review_result", "success"), **kwargs.pop("env_extra", {})}, **kwargs)

    def approval(self):
        return [{"id": 31, "user": {"login": "runeseer[bot]"}, "commit_id": HEAD, "state": "APPROVED", "submitted_at": "2026-09-19T00:02:00Z"}]

    def verdict_comment(self):
        return [{"id": 5, "user": {"login": "runeseer[bot]"}, "updated_at": "2026-09-19T00:02:00Z", "html_url": "https://x/c5",
                 "body": f"<!-- runeseer-summary -->\n<!-- runeseer-verdict sha={HEAD} base={BASE} round=1 verdict=clean restart=none generation=1 -->\n**Looks good.**"}]

    def test_bound_verdict_with_approval_is_green(self):
        run = self.mirror(ledger=recorded_ledger(), reviews=self.approval(), comments=self.verdict_comment())
        self.assertEqual(run.returncode, 0, run.result.stderr + run.stdout)
        self.assertIn("approves this head at ledger generation 1; coverage: paid", run.stdout)

    def test_stand_down_stays_green_on_a_later_event_with_the_coverage_printed(self):
        ledger = recorded_ledger(verdict=None, coverage="free lanes only: prose only", lanes={"runeseer": "skipped"})
        run = self.mirror(review_result="skipped", ledger=ledger)
        self.assertEqual(run.returncode, 0, run.result.stderr + run.stdout)
        self.assertIn("the paid lane stood down; coverage: free lanes only: prose only", run.stdout)

    def test_late_thread_stales_and_dismisses_the_approval(self):
        run = self.mirror(review_result="skipped", ledger=recorded_ledger(), reviews=self.approval(),
                          comments=self.verdict_comment(), threads=[thread("PRRT_late", "cursor", 9)])
        self.assertEqual(run.returncode, 1)
        self.assertIn("a review thread arrived after the ledger was built", run.stdout)
        self.assertIn("-X PUT repos/runedeck/example/pulls/7/reviews/31/dismissals", run.posted)

    def test_body_edit_and_generation_move_stale_the_approval(self):
        edited = self.mirror(review_result="skipped", ledger=recorded_ledger(), reviews=self.approval(),
                             comments=self.verdict_comment(), body=BODY + " edited")
        self.assertEqual(edited.returncode, 1)
        self.assertIn("the pull request body changed", edited.stdout)
        self.assertIn("/dismissals", edited.posted)
        moved = self.mirror(review_result="skipped", ledger=recorded_ledger(generation=2), reviews=self.approval(),
                            comments=self.verdict_comment())
        self.assertEqual(moved.returncode, 1)
        self.assertIn("generation moved past the verdict's", moved.stdout)

    def test_unjudged_rebuild_is_red_without_a_verdict(self):
        run = self.mirror(ledger=recorded_ledger(verdict=None, generation=2))
        self.assertEqual(run.returncode, 1)
        self.assertIn("records no verdict at generation 2", run.stdout)

    def test_untrusted_ledger_never_greens_an_approval(self):
        run = self.mirror(runs_by_id={"77": {"event": "push", "pull_requests": [{"number": 7}]}},
                          ledger=recorded_ledger(), reviews=self.approval(), comments=self.verdict_comment())
        self.assertEqual(run.returncode, 1)
        self.assertIn("no trusted ledger binds it (ledger: none)", run.stdout)

    def test_failed_lane_and_skip_label_read_first(self):
        failed = self.mirror(review_result="failure", ledger=recorded_ledger())
        self.assertEqual(failed.returncode, 1)
        self.assertIn("the lane concluded: failure", failed.stdout)
        skipped = self.mirror(review_result="failure", labels=("skip:runeseer",))
        self.assertEqual(skipped.returncode, 0)


class BreakerStepTests(BlockRunner):
    BREAKER = step_block(WORKFLOW, "- id: breaker", "\n            # The spec's word is binding")

    def test_moved_head_makes_no_remote_change(self):
        run = self.run_block(
            self.BREAKER, live_head="f" * 40,
            env_extra={"RUNESEER_TOKEN": "t", "REVIEW_ROUND": "1", "GENERATION": "1", "FORMATTER_OUTCOME": "success",
                       "ADJUDICATE_OUTCOME": "failure", "PREVALIDATE_OUTCOME": "skipped", "PREPARE_OUTCOME": "skipped",
                       "PUBLISH_OUTCOME": "skipped", "JUDGMENTS_OUTCOME": "skipped", "UPLOAD_OUTCOME": "skipped",
                       "CONSUME_OUTCOME": "success", "RESTART_OUTCOME": "skipped", "PRE_BREAKER_STATUS": "failure",
                       "SUMMARY_COMMENT_ID": "", "RUN_URL": "https://x/run"},
        )
        self.assertEqual(run.returncode, 0, run.result.stderr + run.stdout)
        self.assertIn("This stale circuit breaker made no remote changes.", run.stdout)
        self.assertEqual(run.posted, "")


if __name__ == "__main__":
    unittest.main()
