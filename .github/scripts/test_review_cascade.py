"""Execute workflow shell against raw local GitHub API fixtures."""

import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / "workflows"
CASCADE = (WORKFLOWS / "review-cascade.yaml").read_text(encoding="utf-8")
CURSOR = (WORKFLOWS / "review-cursor.yaml").read_text(encoding="utf-8")
ENTRY = (WORKFLOWS / "review-entry-cascade.yaml").read_text(encoding="utf-8")
HEAD = "0123456789abcdef0123456789abcdef01234567"
CHECK_NAME = "Fixture Core Correctness"

# The fixture applies gh --jq, then the workflow executes its real selectors.
# It does not return preselected providers or preselected commit heads.
MOCK_API = r"""
gh() {
    printf '%s %s\n' "$GH_TOKEN" "$*" >> "$TRACE"
    local request="$*" query="" response="" label="" endpoint="" state="" reads=""
    while [ "$#" -gt 0 ]; do
        if [ "$1" = "--jq" ]; then query=$2; shift; fi
        case "$1" in
            repos/*) endpoint=$1 ;;
            labels\[\]=*) label=${1#labels\[\]=} ;;
        esac
        shift
    done
    case "$request" in
        *'-X POST '*)
            if [ -n "$MOCK_DISPATCH_ERROR" ]; then printf '%s\n' "$MOCK_DISPATCH_ERROR" >&2; return 1; fi
            case "$request" in *'labels[]=review:runeseer'*) : > "$DISPATCHED" ;; esac
            if [ -n "$label" ] && ! jq -e --arg label "$label" 'any(.[]; .name == $label)' "$LABEL_STATE" >/dev/null; then
                state=$(jq --arg label "$label" '. + [{name:$label}]' "$LABEL_STATE")
                printf '%s\n' "$state" > "$LABEL_STATE"
                printf 'labeled:%s\n' "$label" >> "$EVENTS"
                if [ "$label" = "review:macroscope" ] && [ -n "$MOCK_AFTER_DISPATCH" ]; then
                    printf '0\n' > "$PROVIDER_POLL_COUNT"
                    if [ "$MOCK_DISPATCH_DELAY" = 0 ]; then
                        printf '%s\n' "$MOCK_AFTER_DISPATCH" > "$RUN_STATE"
                    fi
                fi
            fi
            response='{}' ;;
        *'-X DELETE '*)
            if [ -n "$MOCK_CONSUME_ERROR" ]; then printf '%s\n' "$MOCK_CONSUME_ERROR" >&2; return 1; fi
            label=${endpoint##*/}
            label=${label//%3A/:}
            if jq -e --arg label "$label" 'any(.[]; .name == $label)' "$LABEL_STATE" >/dev/null; then
                state=$(jq --arg label "$label" 'map(select(.name != $label))' "$LABEL_STATE")
                printf '%s\n' "$state" > "$LABEL_STATE"
                printf 'unlabeled:%s\n' "$label" >> "$EVENTS"
            fi
            response='{}' ;;
        *'/check-runs?'*)
            if [ -n "$MOCK_READ_ERROR" ]; then printf '%s\n' "$MOCK_READ_ERROR" >&2; return 1; fi
            reads=$(<"$READ_COUNT")
            reads=$((reads + 1))
            printf '%s\n' "$reads" > "$READ_COUNT"
            if [ "$MOCK_WITHDRAW_PROVIDER_READ" = "$reads" ]; then
                state=$(jq 'map(select(.name != "review"))' "$LABEL_STATE")
                printf '%s\n' "$state" > "$LABEL_STATE"
            fi
            if [ "$reads" -gt 1 ] && [ -n "$MOCK_ACTIVE_RECOVERY" ]; then
                printf '%s\n' "$MOCK_ACTIVE_RECOVERY" > "$RUN_STATE"
            fi
            reads=$(<"$PROVIDER_POLL_COUNT")
            if [ "$reads" -ge 0 ]; then
                reads=$((reads + 1))
                printf '%s\n' "$reads" > "$PROVIDER_POLL_COUNT"
                if [ "$reads" -ge "$MOCK_DISPATCH_DELAY" ]; then
                    printf '%s\n' "$MOCK_AFTER_DISPATCH" > "$RUN_STATE"
                fi
            fi
            response=$(<"$RUN_STATE") ;;
        *'/pulls/7 '*)
            if [ "$MOCK_MOVE_AFTER" = true ] && [ -f "$DISPATCHED" ]; then
                response='{"head":{"sha":"ffffffffffffffffffffffffffffffffffffffff"}}'
            else
                response=$(jq -n --arg head "$MOCK_HEAD" '{head:{sha:$head}}')
            fi ;;
        *'/issues/7/labels?'*)
            reads=$(<"$LABEL_READ_COUNT")
            reads=$((reads + 1))
            printf '%s\n' "$reads" > "$LABEL_READ_COUNT"
            if [ "$MOCK_WITHDRAW_LABEL_READ" = "$reads" ]; then
                state=$(jq 'map(select(.name != "review"))' "$LABEL_STATE")
                printf '%s\n' "$state" > "$LABEL_STATE"
            fi
            if [ "$MOCK_UPDATE_RUN_AT_LABEL_READ" = "$reads" ]; then
                printf '%s\n' "$MOCK_PRE_DISPATCH_RUNS" > "$RUN_STATE"
            fi
            response=$(<"$LABEL_STATE") ;;
        *) printf 'Unexpected API request: %s\n' "$request" >&2; return 99 ;;
    esac
    if [ -n "$query" ]; then jq -r "$query" <<<"$response"; else printf '%s\n' "$response"; fi
}
sleep() { :; }
"""


def check(conclusion="success", *, name=CHECK_NAME, app="macroscopeapp", head=HEAD,
          status="completed", identifier=1):
    return {
        "id": identifier,
        "name": name,
        "app": {"slug": app},
        "head_sha": head,
        "status": status,
        "conclusion": conclusion,
        "started_at": "2026-09-09T00:00:00Z",
        "html_url": f"https://github.com/runedeck/example/check/{identifier}",
    }


class CascadeTests(unittest.TestCase):
    def run_cascade(self, *, labels=(), runs=None, head=HEAD, fork=False,
                    check_name=CHECK_NAME, dispatch_error="", consume_error="",
                    read_error="", move_after=False, after_dispatch=None, active_recovery=None,
                    event=None, withdraw_label_read=None, withdraw_provider_read=None,
                    dispatch_delay=0, poll_rounds=4, pre_dispatch_runs=None):
        env_block = CASCADE.split("\nenv:\n", 1)[1].split("\njobs:", 1)[0]
        env = os.environ.copy()
        for key, value in re.findall(r"^    ([A-Z_]+): (.+)$", env_block, re.MULTILINE):
            env[key] = value.strip('"')
        label_names = list(dict.fromkeys(labels))
        # Direct body tests begin after the live-label gate has authorized their request.
        if event is None and "review" not in label_names:
            label_names.append("review")
        env.update(
            REPO="runedeck/example", PR="7", HEAD_SHA=HEAD,
            IS_FORK=str(fork).lower(), GH_TOKEN="app-fixture",
            CALLER_TOKEN="workflow-fixture", CHECK_MACROSCOPE=check_name,
            POLL_ROUNDS_MACROSCOPE=str(poll_rounds), MOCK_HEAD=head,
            MOCK_LABELS=json.dumps([{"name": label} for label in label_names]),
            MOCK_RUNS=json.dumps({"check_runs": [check()] if runs is None else runs}),
            MOCK_DISPATCH_ERROR=dispatch_error, MOCK_CONSUME_ERROR=consume_error,
            MOCK_READ_ERROR=read_error, MOCK_MOVE_AFTER=str(move_after).lower(),
            MOCK_AFTER_DISPATCH=json.dumps({"check_runs": after_dispatch}) if after_dispatch is not None else "",
            MOCK_ACTIVE_RECOVERY=json.dumps({"check_runs": active_recovery}) if active_recovery is not None else "",
            MOCK_WITHDRAW_LABEL_READ=str(withdraw_label_read or ""),
            MOCK_WITHDRAW_PROVIDER_READ=str(withdraw_provider_read or ""),
            MOCK_DISPATCH_DELAY=str(dispatch_delay),
            MOCK_UPDATE_RUN_AT_LABEL_READ="4" if pre_dispatch_runs is not None else "",
            MOCK_PRE_DISPATCH_RUNS=json.dumps({"check_runs": pre_dispatch_runs}) if pre_dispatch_runs is not None else "",
        )
        body = CASCADE.split("- name: Run the review cascade", 1)[1].split("run: |\n", 1)[1]
        body = textwrap.dedent(body)
        if event is not None:
            live = CASCADE.split("- id: live", 1)[1].split("run: |\n", 1)[1].split("\n            - id: runewright", 1)[0]
            live = textwrap.dedent(live).replace("${{ github.event.action }}", event)
            live = live.replace("${{ github.repository }}", "runedeck/example")
            live = live.replace("${{ github.event.pull_request.number }}", "7")
            # Execute the actual gate in its own step. A false output skips dispatch.
            body = (
                'cascade_token=$GH_TOKEN\nGH_TOKEN=$CALLER_TOKEN\n(\n' + live + '\n)\n'
                'if ! grep -qx "live=true" "$GITHUB_OUTPUT"; then exit 0; fi\n'
                'GH_TOKEN=$cascade_token\n' + body
            )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "requests"
            env["TRACE"] = str(trace)
            env["DISPATCHED"] = str(Path(directory) / "dispatched")
            for key, name, value in (
                ("LABEL_STATE", "labels.json", env["MOCK_LABELS"]),
                ("RUN_STATE", "runs.json", env["MOCK_RUNS"]),
                ("READ_COUNT", "read-count", "0"),
                ("LABEL_READ_COUNT", "label-read-count", "0"),
                ("PROVIDER_POLL_COUNT", "provider-poll-count", "-1"),
                ("EVENTS", "events", ""),
                ("GITHUB_OUTPUT", "step-output", ""),
            ):
                target = Path(directory) / name
                target.write_text(value, encoding="utf-8")
                env[key] = str(target)
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail"],
                input=MOCK_API + body, text=True,
                capture_output=True, env=env, timeout=20, check=False,
            )
            result.fixture_labels = json.loads(Path(env["LABEL_STATE"]).read_text(encoding="utf-8"))
            result.fixture_events = Path(env["EVENTS"]).read_text(encoding="utf-8").splitlines()
            result.fixture_provider_polls = int(Path(env["PROVIDER_POLL_COUNT"]).read_text(encoding="utf-8"))
            return result, trace.read_text(encoding="utf-8")

    def assert_stopped(self, result, requests):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("labels[]=review:runeseer", requests)
        self.assertNotIn("-X DELETE", requests)

    def test_opened_without_live_label_cannot_dispatch(self):
        result, requests = self.run_cascade(event="opened")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No live review request", result.stdout)
        self.assertNotIn("-X POST", requests)
        self.assertNotIn("/check-runs?", requests)
        self.assertNotIn("-X DELETE", requests)

    def test_opened_with_live_label_follows_current_head_review_checks(self):
        result, requests = self.run_cascade(event="opened", labels=("review",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("/check-runs?", requests)
        self.assertIn("labels[]=review:runeseer", requests)

    def test_opened_with_label_still_rejects_missing_correctness_evidence(self):
        result, requests = self.run_cascade(event="opened", labels=("review",), runs=[])
        self.assert_stopped(result, requests)
        self.assertIn("labels[]=review:macroscope", requests)

    def test_consumed_label_cannot_authorize_a_queued_event(self):
        result, requests = self.run_cascade(event="labeled")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("-X POST", requests)

    def test_mutating_steps_depend_on_the_live_label_gate(self):
        self.assertEqual(CASCADE.count("if: steps.live.outputs.live == 'true'"), 2)

    def test_withdrawn_request_stops_immediately_before_macroscope_dispatch(self):
        result, requests = self.run_cascade(
            event="opened", labels=("review",), runs=[], withdraw_label_read=4,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("labels[]=review:macroscope", requests)
        self.assertNotIn("labels[]=review:runeseer", requests)
        self.assertIn("withdrawn", result.stdout)

    def test_withdrawn_request_stops_runeseer_after_an_existing_review(self):
        result, requests = self.run_cascade(
            event="opened", labels=("review",), withdraw_label_read=4,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("labels[]=review:runeseer", requests)
        self.assertIn("withdrawn", result.stdout)

    def test_withdrawal_does_not_cancel_an_active_provider_round(self):
        result, requests = self.run_cascade(
            event="opened", labels=("review", "review:macroscope"),
            runs=[check(None, status="in_progress")], active_recovery=[check("neutral", identifier=2)],
            withdraw_provider_read=2,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("completed as neutral", result.stdout)
        self.assertNotIn("labels[]=review:macroscope", requests)
        self.assertNotIn("labels[]=review:runeseer", requests)
        self.assertNotIn("-X DELETE", requests)

    def test_withdrawn_skip_path_cannot_start_the_paid_lane(self):
        result, requests = self.run_cascade(
            event="opened", labels=("review", "skip:macroscope"), withdraw_label_read=3,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("labels[]=review:runeseer", requests)
        self.assertIn("withdrawn", result.stdout)

    def test_default_cascade_ignores_optional_failures_and_tokens(self):
        result, requests = self.run_cascade(
            labels=("issue:cursor", "stage:cursor", "review:coderabbit"),
            runs=[check(), check("failure", name="Bugbot", app="cursor", identifier=2)],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("stage 1: macroscope", result.stdout)
        self.assertIn("stage 2: runeseer", result.stdout)
        self.assertIn("labels[]=stage:macroscope", requests)
        self.assertIn("labels[]=review:runeseer", requests)
        self.assertNotIn("@cursor", requests)
        self.assertNotIn("@coderabbit", requests)
        self.assertNotIn("SUMMON_TOKEN", CASCADE)

    def test_legacy_token_input_remains_optional_and_unused(self):
        self.assertRegex(CASCADE, r"RUNEWRIGHT_GITHUB_TOKEN:\s+required: false")
        self.assertNotIn("secrets.RUNEWRIGHT_GITHUB_TOKEN", CASCADE)

    def test_unconfigured_correctness_identity_is_an_explicit_blocker(self):
        result, requests = self.run_cascade(check_name="")
        self.assert_stopped(result, requests)
        self.assertIn("MACROSCOPE_CORRECTNESS_CHECK is unset", result.stdout)
        self.assertNotIn("-X POST", requests)

    def test_approvability_checkbox_is_not_correctness_evidence(self):
        approvability = check(name="Macroscope - Approvability Check")
        approvability["output"] = {"title": "Approved", "summary": "- [x] Eligibility\n- [x] Correctness"}
        result, requests = self.run_cascade(runs=[approvability])
        self.assert_stopped(result, requests)
        self.assertNotIn("labels[]=stage:macroscope", requests)

    def test_known_noncorrectness_names_cannot_be_configured(self):
        for name in ("Macroscope - Approvability Check", "Macroscope - Rust Conventions",
                     "Macroscope - Custom Agent", "Cursor Security Agent: Security Reviewer"):
            with self.subTest(name=name):
                result, requests = self.run_cascade(check_name=name, runs=[check(name=name)])
                self.assert_stopped(result, requests)
                self.assertIn("names an excluded check", result.stdout)

    def test_wrong_provider_is_filtered_by_the_real_selector(self):
        result, requests = self.run_cascade(runs=[check(app="cursor")])
        self.assert_stopped(result, requests)
        self.assertIn("labels[]=review:macroscope", requests)

    def test_custom_agent_cannot_imitate_the_core_check_name(self):
        custom = check()
        custom["output"] = {
            "text": "> [!NOTE]\n> Your check run agent prompt is: "
            "[`.macroscope/check-run-agents/correctness.md`](https://github.com/runedeck/example/blob/main/.macroscope/check-run-agents/correctness.md)"
        }
        result, requests = self.run_cascade(runs=[custom])
        self.assert_stopped(result, requests)
        self.assertNotIn("labels[]=stage:macroscope", requests)

    def test_wrong_head_is_filtered_by_the_real_selector(self):
        result, requests = self.run_cascade(runs=[check(head="f" * 40)])
        self.assert_stopped(result, requests)

    def test_missing_provider_or_head_is_not_review_evidence(self):
        for key in ("app", "head_sha"):
            with self.subTest(key=key):
                run = check()
                del run[key]
                result, requests = self.run_cascade(runs=[run])
                self.assert_stopped(result, requests)

    def test_skipped_review_preserves_request_and_blocks(self):
        result, requests = self.run_cascade(
            runs=[check("skipped")], after_dispatch=[check("skipped", identifier=2)],
        )
        self.assert_stopped(result, requests)
        self.assertIn("ended as skipped", result.stdout)
        self.assertNotIn("labels[]=stage:macroscope", requests)

    def test_neutral_findings_reach_runeseer_without_thread_resolution(self):
        result, requests = self.run_cascade(runs=[check("neutral")])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("completed as neutral", result.stdout)
        self.assertIn("Runeseer will adjudicate its findings", result.stdout)
        self.assertIn("labels[]=review:runeseer", requests)
        self.assertNotIn("graphql", requests)

    def test_old_stage_label_does_not_bypass_current_head_evidence(self):
        result, requests = self.run_cascade(labels=("stage:macroscope",), runs=[])
        self.assert_stopped(result, requests)
        self.assertIn("labels[]=review:macroscope", requests)

    def test_failed_and_timed_out_provider_checks_stop(self):
        for conclusion in ("failure", "timed_out", "cancelled", "action_required"):
            with self.subTest(conclusion=conclusion):
                result, requests = self.run_cascade(
                    runs=[check(conclusion)], after_dispatch=[check(conclusion, identifier=2)],
                )
                self.assert_stopped(result, requests)
                self.assertIn(f"ended as {conclusion}", result.stdout)
                self.assertIn("labels[]=issue:macroscope", requests)

    def test_provider_failure_applies_blocker_with_workflow_token(self):
        result, requests = self.run_cascade(
            runs=[check("failure")], after_dispatch=[check("failure", identifier=2)],
        )
        self.assert_stopped(result, requests)
        self.assertIn("workflow-fixture api -X POST repos/runedeck/example/issues/7/labels -f labels[]=issue:macroscope", requests)
        self.assertNotIn("app-fixture api -X POST repos/runedeck/example/issues/7/labels -f labels[]=issue:macroscope", requests)

    def test_newer_failure_supersedes_earlier_success_on_same_head(self):
        result, requests = self.run_cascade(
            runs=[check(), check("failure", identifier=2)],
            after_dispatch=[check("failure", identifier=3)],
        )
        self.assert_stopped(result, requests)
        self.assertIn("ended as failure", result.stdout)

    def test_delayed_new_check_survives_more_than_three_stale_failure_polls(self):
        result, requests = self.run_cascade(
            labels=("review", "review:macroscope"), runs=[check("failure")],
            after_dispatch=[check(identifier=2)], dispatch_delay=6, poll_rounds=8,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreaterEqual(result.fixture_provider_polls, 6)
        self.assertEqual(result.fixture_events.count("labeled:review:macroscope"), 1)
        self.assertNotIn("labels[]=issue:macroscope", requests)
        self.assertIn("labels[]=review:runeseer", requests)

    def test_old_terminal_check_exhausts_timeout_without_a_new_blocker(self):
        result, requests = self.run_cascade(runs=[check("failure")], poll_rounds=8)
        self.assert_stopped(result, requests)
        self.assertIn("Macroscope correctness did not complete", result.stdout)
        self.assertNotIn("ended as failure", result.stdout)
        self.assertNotIn("labels[]=issue:macroscope", requests)

    def test_old_check_changed_to_success_cannot_satisfy_a_fresh_request(self):
        for conclusion in ("success", "neutral"):
            with self.subTest(conclusion=conclusion):
                result, requests = self.run_cascade(
                    runs=[check("failure")], after_dispatch=[check(conclusion)],
                )
                self.assert_stopped(result, requests)
                self.assertIn("Macroscope correctness did not complete", result.stdout)
                self.assertNotIn("labels[]=stage:macroscope", requests)

    def test_null_baseline_accepts_a_new_check_created_during_dispatch(self):
        result, requests = self.run_cascade(runs=[], after_dispatch=[check()])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("labels[]=review:runeseer", requests)

    def test_baseline_includes_a_check_visible_just_before_dispatch(self):
        result, requests = self.run_cascade(
            event="opened", labels=("review",), runs=[check("failure")],
            pre_dispatch_runs=[check("failure", identifier=2)],
            after_dispatch=[check(identifier=2)],
        )
        self.assert_stopped(result, requests)
        self.assertIn("Macroscope correctness did not complete", result.stdout)
        self.assertIn("labels[]=review:macroscope", requests)
        self.assertNotIn("labels[]=issue:macroscope", requests)

    def test_new_terminal_check_marks_the_provider_blocker(self):
        result, requests = self.run_cascade(
            runs=[check("failure")], after_dispatch=[check("failure", identifier=2)],
        )
        self.assert_stopped(result, requests)
        self.assertIn("ended as failure", result.stdout)
        self.assertIn("labels[]=issue:macroscope", requests)

    def test_observed_active_round_can_fail_without_a_duplicate_request(self):
        result, requests = self.run_cascade(
            runs=[check(None, status="in_progress", identifier=2)],
            active_recovery=[check("failure", identifier=2)],
        )
        self.assert_stopped(result, requests)
        self.assertIn("ended as failure", result.stdout)
        self.assertNotIn("labels[]=review:macroscope", requests)
        self.assertIn("labels[]=issue:macroscope", requests)

    def test_current_running_review_is_not_summoned_twice(self):
        result, requests = self.run_cascade(runs=[check(None, status="in_progress")])
        self.assert_stopped(result, requests)
        self.assertNotIn("labels[]=review:macroscope", requests)

    def test_read_failure_does_not_become_empty_success(self):
        result, requests = self.run_cascade(read_error="gh: unavailable (HTTP 503)")
        self.assert_stopped(result, requests)
        self.assertIn("Cannot read Macroscope correctness checks", result.stdout)

    def test_dispatch_failure_preserves_entry_request(self):
        result, requests = self.run_cascade(runs=[], dispatch_error="gh: forbidden (HTTP 403)")
        self.assert_stopped(result, requests)
        self.assertIn("GitHub denied dispatch", result.stdout)
        self.assertNotIn("Grant issues", result.stdout)

    def test_changed_head_stops_before_any_write(self):
        result, requests = self.run_cascade(head="f" * 40)
        self.assert_stopped(result, requests)
        self.assertIn("The head moved", result.stdout)
        self.assertNotIn("-X POST", requests)

    def test_stale_head_instructs_a_new_event_instead_of_rerun(self):
        result, requests = self.run_cascade(head="f" * 40)
        self.assert_stopped(result, requests)
        self.assertIn("Remove and reapply review on the final head", result.stdout)
        self.assertNotIn("rerun this workflow", result.stdout)

    def test_cleared_blocker_retry_rearms_an_existing_provider_label(self):
        result, requests = self.run_cascade(
            labels=("review", "review:macroscope"), runs=[check("failure")],
            after_dispatch=[check(identifier=2)],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.fixture_events.count("labeled:review:macroscope"), 1)
        self.assertLess(result.fixture_events.index("unlabeled:review:macroscope"),
                        result.fixture_events.index("labeled:review:macroscope"))
        self.assertIn("workflow-fixture api -X DELETE repos/runedeck/example/issues/7/labels/review%3Amacroscope", requests)
        self.assertIn("labels[]=review:runeseer", requests)

    def test_existing_blocker_prevents_failed_provider_redispatch(self):
        result, requests = self.run_cascade(
            labels=("review", "review:macroscope", "issue:macroscope"), runs=[check("failure")],
            after_dispatch=[check(identifier=2)],
        )
        self.assert_stopped(result, requests)
        self.assertNotIn("-X POST", requests)
        self.assertIn("issue:macroscope is present", result.stdout)
        self.assertEqual(result.fixture_events, [])

    def test_completed_recovery_clears_blocker_without_duplicate_review(self):
        for conclusion in ("success", "neutral"):
            with self.subTest(conclusion=conclusion):
                result, requests = self.run_cascade(
                    labels=("review", "review:macroscope", "issue:macroscope"),
                    runs=[check(conclusion)],
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn({"name": "issue:macroscope"}, result.fixture_labels)
                self.assertIn("unlabeled:issue:macroscope", result.fixture_events)
                self.assertNotIn("labeled:review:macroscope", result.fixture_events)
                self.assertNotIn("labels[]=review:macroscope", requests)

    def test_live_recovery_waits_and_clears_blocker_without_resummoning(self):
        result, requests = self.run_cascade(
            labels=("review", "review:macroscope", "issue:macroscope"),
            runs=[check(None, status="in_progress")], active_recovery=[check(identifier=2)],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("labels[]=review:macroscope", requests)
        self.assertNotIn({"name": "issue:macroscope"}, result.fixture_labels)
        self.assertIn("labels[]=review:runeseer", requests)

    def test_failed_provider_label_consumption_has_no_app_fallback(self):
        result, requests = self.run_cascade(
            labels=("review", "review:macroscope"), runs=[check("failure")],
            consume_error="gh: forbidden (HTTP 403)", after_dispatch=[check(identifier=2)],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(requests.count("-X DELETE"), 1)
        self.assertNotIn("app-fixture api -X DELETE", requests)
        self.assertNotIn("labels[]=review:macroscope", requests)
        self.assertIn({"name": "review"}, result.fixture_labels)

    def test_existing_active_provider_label_never_emits_another_request(self):
        result, requests = self.run_cascade(
            labels=("review", "review:macroscope"), runs=[check(None, status="in_progress")],
            active_recovery=[check("neutral", identifier=2)],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("labels[]=review:macroscope", requests)
        self.assertNotIn("labeled:review:macroscope", result.fixture_events)

    def test_provider_failure_blocker_clearance_and_retry_lifecycle(self):
        failed, _ = self.run_cascade(
            labels=("review", "review:macroscope"), runs=[check("failure")],
            after_dispatch=[check("failure", identifier=2)],
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn({"name": "review"}, failed.fixture_labels)
        self.assertIn({"name": "issue:macroscope"}, failed.fixture_labels)
        blocked, blocked_requests = self.run_cascade(
            labels=tuple(label["name"] for label in failed.fixture_labels),
            runs=[check("failure", identifier=2)], after_dispatch=[check(identifier=3)],
        )
        self.assert_stopped(blocked, blocked_requests)
        self.assertEqual(blocked.fixture_events, [])
        recovered, _ = self.run_cascade(
            labels=tuple(label["name"] for label in blocked.fixture_labels if label["name"] != "issue:macroscope"),
            runs=[check("failure", identifier=2)], after_dispatch=[check("neutral", identifier=3)],
        )
        self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        self.assertEqual(recovered.fixture_events.count("labeled:review:macroscope"), 1)
        self.assertIn({"name": "review:runeseer"}, recovered.fixture_labels)

    def test_provider_rearm_add_failure_preserves_the_entry_request(self):
        result, requests = self.run_cascade(
            labels=("review", "review:macroscope"), runs=[check("failure")],
            dispatch_error="gh: denied (HTTP 403)",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn({"name": "review"}, result.fixture_labels)
        self.assertEqual(result.fixture_events, ["unlabeled:review:macroscope"])
        self.assertNotIn("labels[]=review:runeseer", requests)
        self.assertNotIn("app-fixture api -X DELETE", requests)

    def test_head_changed_after_dispatch_preserves_entry(self):
        result, requests = self.run_cascade(move_after=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("labels[]=review:runeseer", requests)
        self.assertNotIn("-X DELETE", requests)

    def test_existing_runeseer_request_emits_a_fresh_event_before_consumption(self):
        result, requests = self.run_cascade(labels=("review", "review:runeseer"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.fixture_events.count("labeled:review:runeseer"), 1)
        self.assertLess(result.fixture_events.index("unlabeled:review:runeseer"),
                        result.fixture_events.index("labeled:review:runeseer"))
        self.assertLess(result.fixture_events.index("labeled:review:runeseer"),
                        result.fixture_events.index("unlabeled:review"))
        self.assertIn("workflow-fixture api -X DELETE repos/runedeck/example/issues/7/labels/review%3Aruneseer", requests)
        self.assertNotIn("app-fixture api -X DELETE", requests)

    def test_runeseer_rearm_delete_failure_preserves_entry(self):
        result, requests = self.run_cascade(
            labels=("review", "review:runeseer"),
            consume_error="gh: forbidden (HTTP 403)",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("labels[]=review:runeseer", requests)
        self.assertIn({"name": "review"}, result.fixture_labels)
        self.assertNotIn("app-fixture api -X DELETE", requests)

    def test_runeseer_rearm_add_failure_preserves_entry(self):
        result, requests = self.run_cascade(
            labels=("review", "review:runeseer", "skip:macroscope"),
            dispatch_error="gh: forbidden (HTTP 403)",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn({"name": "review"}, result.fixture_labels)
        self.assertEqual(result.fixture_events, ["unlabeled:review:runeseer"])
        self.assertNotIn("labels/review ", requests)

    def test_runeseer_blocker_remains_required(self):
        result, requests = self.run_cascade(labels=("issue:rune",))
        self.assert_stopped(result, requests)
        self.assertIn("issue:rune is present", result.stdout)

    def test_successful_dispatch_precedes_workflow_token_consumption(self):
        result, requests = self.run_cascade()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLess(requests.index("labels[]=review:runeseer"), requests.index("-X DELETE"))
        self.assertIn("workflow-fixture api -X DELETE", requests)
        self.assertNotIn("app-fixture api -X DELETE", requests)

    def test_consumption_failure_has_no_app_fallback(self):
        result, requests = self.run_cascade(consume_error="gh: forbidden (HTTP 403)")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(requests.count("-X DELETE"), 1)
        self.assertIn("GitHub denied request consumption (HTTP 403)", result.stdout)
        self.assertNotIn("Grant issues", result.stdout)

    def test_fork_stops_before_paid_adjudication(self):
        result, requests = self.run_cascade(fork=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("The owner must review", result.stdout)
        self.assertNotIn("labels[]=review:runeseer", requests)

    def test_explicit_owner_skip_does_not_claim_review(self):
        result, requests = self.run_cascade(labels=("skip:macroscope",), check_name="")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No Macroscope review is recorded", result.stdout)
        self.assertNotIn("labels[]=stage:macroscope", requests)
        self.assertIn("labels[]=review:runeseer", requests)

    def test_unlabeled_consumption_cannot_cancel_the_caller(self):
        cancel_line = next(line for line in ENTRY.splitlines() if "cancel-in-progress:" in line)
        self.assertIn("github.event.action == 'labeled'", cancel_line)
        self.assertNotIn("unlabeled", cancel_line)
        self.assertIn("pull-requests: write", ENTRY)


class CursorTests(unittest.TestCase):
    def run_summon(self, *, token="owner-fixture", head=HEAD, error="", consume_error="", move_after=False,
                   labels=("review:cursor",), label_pages=None, label_error=""):
        env = os.environ.copy()
        env.update(
            REPO="runedeck/example",
            PR="7",
            HEAD_SHA=HEAD,
            GH_TOKEN="workflow-fixture",
            SUMMON_TOKEN=token,
            SUMMON_COMMENT="@cursor review",
            MOCK_HEAD=head,
            MOCK_ERROR=error,
            MOCK_CONSUME_ERROR=consume_error,
            MOCK_MOVE_AFTER=str(move_after).lower(),
            MOCK_CURSOR_LABEL_PAGES="\n".join(
                json.dumps([{"name": name} for name in page])
                for page in (label_pages if label_pages is not None else [labels])
            ),
            MOCK_CURSOR_LABEL_ERROR=label_error,
        )
        mock = r'''
gh() {
    printf '%s %s\n' "$GH_TOKEN" "$*" >> "$TRACE"
    local request="$*" query=""
    while [ "$#" -gt 0 ]; do
        if [ "$1" = "--jq" ]; then query=$2; shift; fi
        shift
    done
    case "$request" in
        *'/issues/7/labels?'*)
            if [ -n "$MOCK_CURSOR_LABEL_ERROR" ]; then printf '%s\n' "$MOCK_CURSOR_LABEL_ERROR" >&2; return 1; fi
            jq -r "$query" <<<"$MOCK_CURSOR_LABEL_PAGES" ;;
        *'/pulls/7 '*)
            if [ "$MOCK_MOVE_AFTER" = true ] && [ -f "$DISPATCHED" ]; then
                printf 'ffffffffffffffffffffffffffffffffffffffff\n'
            else
                printf '%s\n' "$MOCK_HEAD"
            fi ;;
        *'-X POST '*'/comments '*)
            if [ -n "$MOCK_ERROR" ]; then printf '%s\n' "$MOCK_ERROR" >&2; return 1; fi
            : > "$DISPATCHED"
            printf '12345\n' ;;
        *'-X DELETE '*)
            if [ -n "$MOCK_CONSUME_ERROR" ]; then printf '%s\n' "$MOCK_CONSUME_ERROR" >&2; return 1; fi
            printf '{}\n' ;;
        *) printf 'Unexpected request: %s\n' "$*" >&2; return 99 ;;
    esac
}
'''
        body = CURSOR.split("- name: Dispatch Bugbot and consume the request", 1)[1].split("run: |\n", 1)[1]
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "requests"
            env["TRACE"] = str(trace)
            env["DISPATCHED"] = str(Path(directory) / "dispatched")
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail"],
                input=mock + textwrap.dedent(body), text=True, capture_output=True,
                env=env, timeout=20, check=False,
            )
            return result, trace.read_text() if trace.exists() else ""

    def test_missing_credentials_preserve_the_request(self):
        result, requests = self.run_summon(token="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is empty", result.stdout)
        self.assertEqual(requests, "")

    def test_withdrawn_cursor_request_cannot_dispatch(self):
        result, requests = self.run_summon(labels=())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("withdrawn", result.stdout)
        self.assertNotIn("-X POST", requests)
        self.assertNotIn("-X DELETE", requests)

    def test_cursor_final_authorization_reads_all_label_pages(self):
        result, requests = self.run_summon(label_pages=[("area:docs",), ("review:cursor",)])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("workflow-fixture api --paginate repos/runedeck/example/issues/7/labels?per_page=100", requests)
        self.assertIn("-X POST", requests)

    def test_cursor_label_read_failure_preserves_request(self):
        result, requests = self.run_summon(label_error="gh: denied (HTTP 403)")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("-X POST", requests)
        self.assertNotIn("-X DELETE", requests)

    def test_invalid_credentials_preserve_the_request(self):
        result, requests = self.run_summon(error="gh: Bad credentials (HTTP 401)")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("authentication (HTTP 401)", result.stdout)
        self.assertNotIn("-X DELETE", requests)

    def test_forbidden_request_does_not_guess_missing_credentials(self):
        result, requests = self.run_summon(error="gh: Resource not accessible (HTTP 403)")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("denied the Bugbot summon (HTTP 403)", result.stdout)
        self.assertNotIn("expired", result.stdout)
        self.assertNotIn("-X DELETE", requests)

    def test_uncertain_dispatch_preserves_request_and_requires_inspection(self):
        result, requests = self.run_summon(error="connection reset")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Inspect the existing comments", result.stdout)
        self.assertNotIn("-X DELETE", requests)

    def test_changed_head_prevents_dispatch(self):
        result, requests = self.run_summon(head="f" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("head changed", result.stdout)
        self.assertNotIn("-X POST", requests)
        self.assertNotIn("-X DELETE", requests)

    def test_changed_head_after_dispatch_preserves_request(self):
        result, requests = self.run_summon(move_after=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("-X POST", requests)
        self.assertNotIn("-X DELETE", requests)

    def test_successful_dispatch_precedes_workflow_token_consumption(self):
        result, requests = self.run_summon()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLess(requests.index("-X POST"), requests.index("-X DELETE"))
        self.assertIn("owner-fixture api -X POST", requests)
        self.assertIn("workflow-fixture api -X DELETE", requests)
        self.assertIn("Dispatch does not prove a completed review", result.stdout)

    def test_consumption_failure_has_no_app_token_fallback(self):
        result, requests = self.run_summon(consume_error="gh: Resource not accessible (HTTP 403)")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(requests.count("-X DELETE"), 1)
        self.assertIn("summon succeeded", result.stdout)
        self.assertNotIn("owner-fixture api -X DELETE", requests)

    def test_already_consumed_request_is_successful(self):
        result, requests = self.run_summon(consume_error="gh: Not Found (HTTP 404)")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(requests.count("-X POST"), 1)


if __name__ == "__main__":
    unittest.main()
