#!/usr/bin/env python3
"""Test the deterministic Runeseer summary contract."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("runeseer_summary.py")
SPEC = importlib.util.spec_from_file_location("runeseer_summary", SCRIPT)
assert SPEC and SPEC.loader
SUMMARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUMMARY)

SHA = "0123456789abcdef0123456789abcdef01234567"
RUN_URL = "https://github.com/runedeck/seer/actions/runs/123"
BASE = "abcdef0123456789abcdef0123456789abcdef01"
BODY = f"<!-- runeseer-review -->\n<!-- runeseer-verdict sha={SHA} base={BASE} round=1 verdict=clean -->\n**Looks good.** The change is correct."


def verdict(*, findings=None, restart="none", round_number=1):
    findings = [] if findings is None else findings
    return {
        "sha": SHA,
        "base": BASE,
        "round": round_number,
        "verdict": "clean" if not findings else "findings",
        "count": len(findings),
        "restart": restart,
        "nonfinding_issue_comment_ids": [],
        "findings": findings,
        "lane_judgments": [],
    }


class SummaryTests(unittest.TestCase):
    def format_case(self, data, summary):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            verdict_path = root / "verdict.json"
            summary_path = root / "summary.md"
            verdict_path.write_text(json.dumps(data), encoding="utf-8")
            summary_path.write_text(summary, encoding="utf-8")
            return SUMMARY.format_review(
                verdict_path,
                summary_path,
                SHA,
                data["round"],
                RUN_URL,
            )

    def test_clean_summary_has_markers_and_footer(self):
        body = self.format_case(
            verdict(),
            "**Looks good.** The installer reports its executable path after every successful setup.",
        )
        self.assertIn("<!-- runeseer-review -->", body)
        self.assertIn(
            f"<!-- runeseer-verdict sha={SHA} base=abcdef0123456789abcdef0123456789abcdef01 round=1 verdict=clean restart=none -->",
            body,
        )
        self.assertIn("No open findings", body)
        self.assertIn("Reviewed `01234567`", body)

    def test_clean_summary_renders_headline_without_table(self):
        body = self.format_case(
            verdict(),
            "**Looks good.** The installer reports its executable path after every successful setup.",
        )
        self.assertIn("### Runeseer review — clean", body)
        self.assertNotIn("| Risk |", body)

    def test_findings_summary_renders_risk_table(self):
        data = verdict(
            findings=[
                {
                    "path": "install-tools",
                    "line": 227,
                    "summary": "Success skips path advice",
                    "lane": "runeseer",
                    "judgment": "confirmed",
                    "severity": "medium",
                },
                {
                    "path": "publish.sh",
                    "line": 12,
                    "summary": "Token echoed | into the log",
                    "lane": "runeseer",
                    "judgment": "confirmed",
                    "severity": "high",
                },
            ]
        )
        body = self.format_case(
            data,
            "**Request changes.** `install-tools:227` returns before it reports the required executable path.",
        )
        self.assertIn("### Runeseer review — 2 open findings", body)
        lines = body.splitlines()
        self.assertIn("| Risk | Finding | Location |", lines)
        high = lines.index("| High | Token echoed \\| into the log | `publish.sh:12` |")
        medium = lines.index("| Medium | Success skips path advice | `install-tools:227` |")
        self.assertLess(high, medium)

    def test_findings_summary_uses_array_length(self):
        data = verdict(
            findings=[
                {
                    "path": "install-tools",
                    "line": 227,
                    "summary": "Success skips path advice",
                    "lane": "runeseer",
                    "judgment": "confirmed",
                    "severity": "medium",
                }
            ]
        )
        body = self.format_case(
            data,
            "**Request changes.** `install-tools:227` returns before it reports the required executable path.",
        )
        self.assertIn("1 open", body)
        self.assertIn("verdict=findings", body)

    def test_restart_requires_request_changes_language(self):
        data = verdict(restart="cursor")
        body = self.format_case(
            data,
            "**Request changes.** Cursor and Macroscope will review this head again because the workflow structure changed.",
        )
        self.assertIn("No open findings", body)
        self.assertIn("verdict=clean restart=cursor", body)

    def test_internal_headings_are_rejected(self):
        with self.assertRaises(SUMMARY.SummaryError):
            self.format_case(
                verdict(),
                "**Looks good.** The checksum digest is correct.\n\n#### Digest",
            )

    def test_normal_digest_word_is_allowed(self):
        body = self.format_case(
            verdict(),
            "**Looks good.** The checksum digest matches the release archive.",
        )
        self.assertIn("checksum digest", body)

    def test_summary_limits_are_enforced(self):
        bullets = "\n".join(f"- Note {number}." for number in range(4))
        with self.assertRaises(SUMMARY.SummaryError):
            self.format_case(
                verdict(),
                f"**Looks good.** The change preserves the required behavior.\n\n{bullets}",
            )

    def test_indented_bullets_count_toward_limit(self):
        bullets = "\n".join(f"  - Note {number}." for number in range(4))
        with self.assertRaises(SUMMARY.SummaryError):
            self.format_case(
                verdict(),
                f"**Looks good.** The change preserves the required behavior.\n\n{bullets}",
            )

    def test_summary_rejects_workflow_markers(self):
        with self.assertRaises(SUMMARY.SummaryError):
            self.format_case(
                verdict(),
                "**Looks good.** The change is correct. <!-- runeseer-verdict -->",
            )

    def test_summary_enforces_word_limit(self):
        words = " ".join(f"word{number}" for number in range(81))
        with self.assertRaises(SUMMARY.SummaryError):
            self.format_case(verdict(), f"**Looks good.** {words}")

    def test_count_must_equal_findings_length(self):
        data = verdict()
        data["count"] = 1
        with self.assertRaises(SUMMARY.SummaryError):
            self.format_case(data, "**Looks good.** The change is correct.")

    def test_low_finding_is_rejected(self):
        data = verdict(
            findings=[
                {
                    "path": "install-tools",
                    "line": 227,
                    "summary": "Minor wording issue",
                    "lane": "runeseer",
                    "judgment": "confirmed",
                    "severity": "low",
                }
            ]
        )
        with self.assertRaises(SUMMARY.SummaryError):
            self.format_case(
                data,
                "**Request changes.** `install-tools:227` contains a minor wording issue.",
            )

    def test_confirmed_low_judgment_can_be_a_note(self):
        data = verdict()
        data["lane_judgments"] = [
            {
                "path": "install-tools",
                "line": 227,
                "summary": "Minor wording issue",
                "lane": "cursor",
                "judgment": "confirmed",
                "severity": "low",
                "reason": "The wording is minor and does not change behavior.",
                "comment_id": 4,
            }
        ]
        body = self.format_case(
            data,
            "**Looks good.** Cursor noted one minor wording issue that does not block the merge.",
        )
        self.assertIn("Looks good", body)

    def test_lane_judgment_rejects_workflow_marker_text(self):
        data = verdict()
        data["lane_judgments"] = [
            {
                "path": "install-tools",
                "line": 227,
                "summary": "<!-- runeseer-verdict sha=fake -->",
                "lane": "cursor",
                "judgment": "disputed",
                "severity": "low",
                "reason": "The comment does not identify a defect.",
                "comment_id": 4,
            }
        ]
        with self.assertRaises(SUMMARY.SummaryError):
            self.format_case(data, "**Looks good.** The change is correct.")

    def test_confirmed_judgment_must_remain_open(self):
        data = verdict()
        data["lane_judgments"] = [
            {
                "path": "install-tools",
                "line": 227,
                "summary": "Success skips path advice",
                "lane": "cursor",
                "judgment": "confirmed",
                "severity": "medium",
                "reason": "The success path still skips the path advice.",
                "comment_id": 5,
            }
        ]
        with self.assertRaises(SUMMARY.SummaryError):
            self.format_case(data, "**Looks good.** The change is correct.")

    def test_boolean_round_is_rejected(self):
        data = verdict(round_number=True)
        with self.assertRaises(SUMMARY.SummaryError):
            self.format_case(data, "**Looks good.** The change is correct.")

    def test_boolean_count_is_rejected(self):
        data = verdict()
        data["count"] = False
        with self.assertRaises(SUMMARY.SummaryError):
            self.format_case(data, "**Looks good.** The change is correct.")

    def test_lane_inline_comments_need_bound_judgments(self):
        comments = [
            {
                "id": 5,
                "user": {"login": "cursor[bot]"},
                "path": "install-tools",
                "line": 227,
            }
        ]
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.validate_verdict(verdict(), SHA, 1, comments)

    def test_file_level_comment_needs_a_judgment(self):
        comments = [
            {
                "id": 6,
                "user": {"login": "cursor[bot]"},
                "path": "install-tools",
                "subject_type": "file",
            }
        ]
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.validate_verdict(verdict(), SHA, 1, comments)

        data = verdict()
        data["lane_judgments"] = [
            {
                "path": "install-tools",
                "line": 0,
                "summary": "Policy file is required",
                "lane": "cursor",
                "judgment": "disputed",
                "severity": "medium",
                "reason": "The replacement policy remains in the file.",
                "comment_id": 6,
            }
        ]
        self.assertEqual(SUMMARY.validate_verdict(data, SHA, 1, comments), data)

    def test_lane_judgment_cannot_claim_another_comment(self):
        data = verdict()
        data["lane_judgments"] = [
            {
                "path": "install-tools",
                "line": 227,
                "summary": "Minor wording issue",
                "lane": "cursor",
                "judgment": "disputed",
                "severity": "low",
                "reason": "The executable path is already reported.",
                "comment_id": 9,
            }
        ]
        comments = [
            {
                "id": 5,
                "user": {"login": "cursor[bot]"},
                "path": "install-tools",
                "line": 227,
            }
        ]
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.validate_verdict(data, SHA, 1, comments)

    def test_lane_judgment_accepts_its_source_comment(self):
        data = verdict()
        data["lane_judgments"] = [
            {
                "path": "install-tools",
                "line": 227,
                "summary": "Minor wording issue",
                "lane": "cursor",
                "judgment": "disputed",
                "severity": "low",
                "reason": "The executable path is already reported.",
                "comment_id": 5,
            }
        ]
        comments = [
            {
                "id": 5,
                "user": {"login": "cursor[bot]"},
                "path": "install-tools",
                "line": 227,
            }
        ]
        self.assertEqual(SUMMARY.validate_verdict(data, SHA, 1, comments), data)

    def test_runeseer_ledger_judgment_skips_lane_bindings(self):
        data = verdict()
        data["lane_judgments"] = [
            {
                "path": "install-tools",
                "line": 227,
                "summary": "Per-skill schema never runs",
                "lane": "runeseer",
                "judgment": "already addressed",
                "severity": "high",
                "reason": "HEAD validates every entrypoint against its nearest schema.",
                "comment_id": 99,
            }
        ]
        comments = []
        self.assertEqual(SUMMARY.validate_verdict(data, SHA, 1, comments), data)

    def test_confirmed_runeseer_recheck_keeps_open_finding_valid(self):
        data = verdict(
            findings=[
                {
                    "path": "install-tools",
                    "line": 227,
                    "summary": "Setup omits its executable path",
                    "lane": "runeseer",
                    "judgment": "confirmed",
                    "severity": "medium",
                }
            ]
        )
        data["lane_judgments"] = [
            {
                "path": "install-tools",
                "line": 227,
                "summary": "Setup omits its executable path",
                "lane": "runeseer",
                "judgment": "confirmed",
                "severity": "medium",
                "reason": "The defect is still present at HEAD.",
                "comment_id": 99,
            }
        ]
        self.assertEqual(SUMMARY.validate_verdict(data, SHA, 1, []), data)

    def test_confirmed_runeseer_recheck_must_remain_open(self):
        data = verdict()
        data["lane_judgments"] = [
            {
                "path": "install-tools",
                "line": 227,
                "summary": "Setup omits its executable path",
                "lane": "runeseer",
                "judgment": "confirmed",
                "severity": "medium",
                "reason": "The defect is still present at HEAD.",
                "comment_id": 99,
            }
        ]
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.validate_verdict(data, SHA, 1, [])

    def test_issue_comments_must_be_acknowledged(self):
        comments = [{"id": 7, "user": {"login": "cursor[bot]"}}]
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.validate_verdict(verdict(), SHA, 1, comments)

        data = verdict()
        data["nonfinding_issue_comment_ids"] = [7]
        self.assertEqual(SUMMARY.validate_verdict(data, SHA, 1, comments), data)

    def test_new_runeseer_comment_needs_an_open_finding(self):
        comments = [
            {
                "id": 11,
                "user": {"login": "runeseer[bot]"},
                "path": "install-tools",
                "line": 227,
                "body": "**Medium** — The setup omits its executable path.",
            }
        ]
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.validate_verdict(verdict(), SHA, 1, None, comments)

    def test_new_runeseer_comment_matches_its_finding(self):
        finding = {
            "path": "install-tools",
            "line": 227,
            "summary": "Setup omits executable path",
            "lane": "runeseer",
            "judgment": "confirmed",
            "severity": "medium",
            "comment_id": 11,
        }
        data = verdict(findings=[finding])
        comments = [
            {
                "id": 11,
                "user": {"login": "runeseer[bot]"},
                "path": "install-tools",
                "line": 227,
                "body": "**Medium** — The setup omits its executable path.",
            }
        ]
        self.assertEqual(SUMMARY.validate_verdict(data, SHA, 1, None, comments), data)

    @staticmethod
    def own_finding(comment_id=None):
        return {
            "path": "install-tools",
            "line": 227,
            "summary": "Setup omits executable path",
            "lane": "runeseer",
            "judgment": "confirmed",
            "severity": "medium",
            "comment_id": comment_id,
        }

    @staticmethod
    def posted_comment(comment_id):
        return {
            "id": comment_id,
            "user": {"login": "runeseer[bot]"},
            "path": "install-tools",
            "line": 227,
            "body": "**Medium** — The setup omits its executable path.",
        }

    def test_binding_fills_the_comment_id_by_anchor(self):
        finding = self.own_finding()
        SUMMARY.validate_verdict(verdict(findings=[finding]), SHA, 1, None,
                                 [self.posted_comment(11)])
        self.assertEqual(finding["comment_id"], 11)

    def test_a_novel_finding_without_a_posted_comment_is_rejected(self):
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.validate_verdict(verdict(findings=[self.own_finding()]), SHA, 1, None, [])

    def test_an_ambiguous_anchor_binding_is_rejected(self):
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.validate_verdict(verdict(findings=[self.own_finding()]), SHA, 1, None,
                                     [self.posted_comment(11), self.posted_comment(12)])

    def test_a_carried_finding_rebinds_to_a_fresh_marked_comment(self):
        finding = self.own_finding(comment_id=999)
        SUMMARY.validate_verdict(verdict(findings=[finding]), SHA, 1, None,
                                 [self.posted_comment(12)], [self.own_finding(comment_id=999)])
        self.assertEqual(finding["comment_id"], 12)

    def test_the_footer_carries_session_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "verdict.json").write_text(json.dumps(verdict()), encoding="utf-8")
            (root / "summary.md").write_text("**Looks good.** Nothing blocks.", encoding="utf-8")
            body = SUMMARY.format_review(root / "verdict.json", root / "summary.md", SHA, 1,
                                         RUN_URL, session_stats="$1.23 · 1m35s · 24 turns")
        self.assertIn(" · $1.23 · 1m35s · 24 turns", body)

    def test_runeseer_finding_needs_new_or_carried_evidence(self):
        finding = {
            "path": "install-tools",
            "line": 227,
            "summary": "Setup omits executable path",
            "lane": "runeseer",
            "judgment": "confirmed",
            "severity": "medium",
            "comment_id": 999,
        }
        data = verdict(findings=[finding])
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.validate_verdict(data, SHA, 1, None, [])
        self.assertEqual(
            SUMMARY.validate_verdict(data, SHA, 1, None, [], [finding]), data
        )

    def test_format_accepts_carried_runeseer_evidence(self):
        finding = {
            "path": "install-tools",
            "line": 227,
            "summary": "Setup omits executable path",
            "lane": "runeseer",
            "judgment": "confirmed",
            "severity": "medium",
            "comment_id": 999,
        }
        data = verdict(findings=[finding])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            verdict_path = root / "verdict.json"
            summary_path = root / "summary.md"
            lane_path = root / "lanes.json"
            comments_path = root / "runeseer.json"
            previous_path = root / "previous.json"
            verdict_path.write_text(json.dumps(data), encoding="utf-8")
            summary_path.write_text(
                "**Request changes.** `install-tools:227` omits the executable path.",
                encoding="utf-8",
            )
            lane_path.write_text("[]", encoding="utf-8")
            comments_path.write_text("[]", encoding="utf-8")
            previous_path.write_text(
                json.dumps({"findings": [finding]}), encoding="utf-8"
            )

            body = SUMMARY.format_review(
                verdict_path,
                summary_path,
                SHA,
                1,
                RUN_URL,
                [lane_path],
                [comments_path],
                previous_path,
            )

        self.assertIn("1 open", body)

    def test_round_migration_uses_highest_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "previous.json"
            path.write_text(json.dumps({"round": 2}), encoding="utf-8")
            self.assertEqual(SUMMARY.next_round(path, 5, 0), 6)
            self.assertEqual(SUMMARY.next_round(path, 1, 0), 3)
            path.write_text(json.dumps({}), encoding="utf-8")
            self.assertEqual(SUMMARY.next_round(path, 2, 0), 3)
            self.assertEqual(SUMMARY.next_round(path, 2, 6), 7)

    def test_marker_round_uses_only_the_current_base(self):
        old_base = "f" * 40
        history = "\n".join(
            (
                BODY.replace("round=1", "round=2"),
                BODY.replace(BASE, old_base).replace("round=1", "round=7"),
            )
        )
        self.assertEqual(SUMMARY.marker_round(history, BASE), 2)

    @patch.object(SUMMARY, "run_gh")
    def test_publish_updates_newest_matching_comment(self, run_gh):
        comments = [
            {
                "id": 9,
                "user": {"login": "runeseer[bot]"},
                "body": "<!-- runeseer-review -->\n<!-- runeseer-verdict sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa base=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb round=0 verdict=clean -->\nold",
                "created_at": "2026-08-14T02:00:00Z",
            },
            {
                "id": 8,
                "user": {"login": "someone"},
                "body": "<!-- runeseer-review -->\nclaim",
                "created_at": "2026-08-14T03:00:00Z",
            },
        ]
        read = SUMMARY.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="\n".join(json.dumps(comment) for comment in comments),
            stderr="",
        )
        write = SUMMARY.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"id": 9, "body": BODY}),
            stderr="",
        )
        live = SUMMARY.subprocess.CompletedProcess(
            args=[], returncode=0, stdout=f"{SHA}\t{BASE}\n", stderr=""
        )
        run_gh.side_effect = [read, live, write]

        action = SUMMARY.publish_summary(BODY, "runedeck/seer", 7, "runeseer[bot]")

        self.assertEqual(action, "updated")
        self.assertIn(
            "repos/runedeck/seer/issues/comments/9", run_gh.call_args_list[2].args[0]
        )

    @patch.object(SUMMARY, "run_gh")
    def test_publish_selects_newest_matching_comment(self, run_gh):
        comments = [
            {
                "id": 2,
                "created_at": "2026-08-14T02:00:00Z",
                "user": {"login": "runeseer[bot]"},
                "body": "<!-- runeseer-review -->\n<!-- runeseer-verdict sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa base=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb round=0 verdict=clean -->\nnew",
            },
            {
                "id": 1,
                "created_at": "2026-08-14T01:00:00Z",
                "user": {"login": "runeseer[bot]"},
                "body": "<!-- runeseer-review -->\n<!-- runeseer-verdict sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa base=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb round=0 verdict=clean -->\nold",
            },
        ]
        read = SUMMARY.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="\n".join(json.dumps(comment) for comment in reversed(comments)),
            stderr="",
        )
        write = SUMMARY.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"id": 2, "body": BODY}),
            stderr="",
        )
        live = SUMMARY.subprocess.CompletedProcess(
            args=[], returncode=0, stdout=f"{SHA}\t{BASE}\n", stderr=""
        )
        run_gh.side_effect = [read, live, write]

        SUMMARY.publish_summary(BODY, "runedeck/seer", 7, "runeseer[bot]")

        self.assertIn(
            "repos/runedeck/seer/issues/comments/2", run_gh.call_args_list[2].args[0]
        )

    @patch.object(SUMMARY, "run_gh")
    def test_publish_selects_highest_round_before_creation_time(self, run_gh):
        comments = [
            {
                "id": 2,
                "created_at": "2026-08-14T02:00:00Z",
                "user": {"login": "runeseer[bot]"},
                "body": BODY.replace("round=1", "round=3"),
            },
            {
                "id": 1,
                "created_at": "2026-08-14T01:00:00Z",
                "user": {"login": "runeseer[bot]"},
                "body": BODY.replace("round=1", "round=5"),
            },
        ]
        read = SUMMARY.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="\n".join(json.dumps(comment) for comment in comments),
            stderr="",
        )
        run_gh.return_value = read

        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.publish_summary(
                BODY.replace("round=1", "round=4"),
                "runedeck/seer",
                7,
                "runeseer[bot]",
            )
        self.assertEqual(run_gh.call_count, 1)

    @patch.object(SUMMARY, "run_gh")
    def test_publish_resets_round_for_a_new_base(self, run_gh):
        new_base = "c" * 40
        candidate = BODY.replace(BASE, new_base)
        comment = {
            "id": 9,
            "created_at": "2026-08-14T02:00:00Z",
            "user": {"login": "runeseer[bot]"},
            "body": BODY.replace("round=1", "round=5"),
        }
        read = SUMMARY.subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(comment), stderr=""
        )
        live = SUMMARY.subprocess.CompletedProcess(
            args=[], returncode=0, stdout=f"{SHA}\t{new_base}\n", stderr=""
        )
        write = SUMMARY.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"id": 9, "body": candidate}),
            stderr="",
        )
        run_gh.side_effect = [read, live, write]

        action = SUMMARY.publish_summary(candidate, "runedeck/seer", 7, "runeseer[bot]")

        self.assertEqual(action, "updated")
        self.assertIn(
            "repos/runedeck/seer/issues/comments/9", run_gh.call_args_list[2].args[0]
        )

    @patch.object(SUMMARY, "run_gh")
    def test_publish_creates_when_marker_is_absent(self, run_gh):
        read = SUMMARY.subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        write = SUMMARY.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"id": 9, "body": BODY}),
            stderr="",
        )
        live = SUMMARY.subprocess.CompletedProcess(
            args=[], returncode=0, stdout=f"{SHA}\t{BASE}\n", stderr=""
        )
        run_gh.side_effect = [read, live, write]

        action = SUMMARY.publish_summary(BODY, "runedeck/seer", 7, "runeseer[bot]")

        self.assertEqual(action, "created")
        self.assertIn(
            "repos/runedeck/seer/issues/7/comments", run_gh.call_args_list[2].args[0]
        )

    @patch.object(SUMMARY, "run_gh")
    def test_newer_round_stops_publication(self, run_gh):
        comment = {
            "id": 9,
            "created_at": "2026-08-14T02:00:00Z",
            "user": {"login": "runeseer[bot]"},
            "body": BODY.replace("round=1", "round=2"),
        }
        read = SUMMARY.subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(comment), stderr=""
        )
        run_gh.return_value = read

        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.publish_summary(BODY, "runedeck/seer", 7, "runeseer[bot]")
        self.assertEqual(run_gh.call_count, 1)

    @patch.object(SUMMARY, "run_gh")
    def test_head_change_stops_publication(self, run_gh):
        read = SUMMARY.subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        live = SUMMARY.subprocess.CompletedProcess(
            args=[], returncode=0, stdout=f"{'f' * 40}\t{BASE}\n", stderr=""
        )
        run_gh.side_effect = [read, live]

        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.publish_summary(BODY, "runedeck/seer", 7, "runeseer[bot]")
        self.assertEqual(run_gh.call_count, 2)

    @patch.object(SUMMARY, "run_gh")
    def test_comment_read_failure_stops_publication(self, run_gh):
        run_gh.return_value = SUMMARY.subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="API failed"
        )
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.publish_summary("body", "runedeck/seer", 7, "runeseer[bot]")


if __name__ == "__main__":
    unittest.main()
