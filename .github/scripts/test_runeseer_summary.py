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
WORKFLOW = SCRIPT.parent.parent / "workflows" / "review-correctness.yaml"
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
        medium = lines.index(
            "| Medium | Success skips path advice | `install-tools:227` |"
        )
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

    def test_internal_headings_use_normalized_summary(self):
        invalid = "**Looks good.** The checksum digest is correct.\n\n#### Digest"
        body = self.format_case(verdict(), invalid)
        self.assertIn(SUMMARY.normalized_summary(verdict()), body)
        self.assertNotIn("checksum digest", body)
        self.assertNotIn("#### Digest", body)

    def test_normal_digest_word_is_allowed(self):
        body = self.format_case(
            verdict(),
            "**Looks good.** The checksum digest matches the release archive.",
        )
        self.assertIn("checksum digest", body)

    def test_excess_bullets_use_normalized_summary(self):
        bullets = "\n".join(f"- Note {number}." for number in range(4))
        body = self.format_case(
            verdict(),
            f"**Looks good.** The change preserves the required behavior.\n\n{bullets}",
        )
        self.assertIn(SUMMARY.normalized_summary(verdict()), body)
        self.assertNotIn("Note 0", body)

    def test_excess_indented_bullets_use_normalized_summary(self):
        bullets = "\n".join(f"  - Note {number}." for number in range(4))
        body = self.format_case(
            verdict(),
            f"**Looks good.** The change preserves the required behavior.\n\n{bullets}",
        )
        self.assertIn(SUMMARY.normalized_summary(verdict()), body)
        self.assertNotIn("Note 0", body)

    def test_summary_markers_use_normalized_summary(self):
        invalid = "**Looks good.** The change is correct. <!-- runeseer-verdict -->"
        body = self.format_case(verdict(), invalid)
        self.assertIn(SUMMARY.normalized_summary(verdict()), body)
        self.assertNotIn("The change is correct.", body)

    def test_clean_summary_over_word_limit_uses_safe_fallback(self):
        words = " ".join(f"word{number}" for number in range(81))
        body = self.format_case(verdict(), f"**Looks good.** {words}")
        self.assertIn(
            "**Looks good.** The review found no blocking correctness defects.",
            body,
        )
        self.assertNotIn("word80", body)

    def test_one_oversized_token_uses_safe_fallback(self):
        token = "a" * (SUMMARY.SUMMARY_BYTE_LIMIT + 1)
        body = self.format_case(verdict(), f"**Looks good.** {token}")
        self.assertIn(SUMMARY.normalized_summary(verdict()), body)
        self.assertNotIn(token, body)

    def test_oversized_nonword_unicode_uses_safe_fallback(self):
        text = "💥" * (SUMMARY.SUMMARY_BYTE_LIMIT // 4 + 1)
        body = self.format_case(verdict(), f"**Looks good.** {text}")
        self.assertIn(SUMMARY.normalized_summary(verdict()), body)
        self.assertNotIn(text, body)

    def test_plain_text_byte_limit_accepts_boundary_and_rejects_overflow(self):
        boundary = "x" * SUMMARY.MODEL_TEXT_BYTE_LIMIT
        self.assertEqual(SUMMARY.validate_plain_text(boundary, "field"), boundary)
        with self.assertRaisesRegex(SUMMARY.SummaryError, "4096 UTF-8 bytes"):
            SUMMARY.validate_plain_text(boundary + "x", "field")

        unicode_boundary = "💥" * (SUMMARY.MODEL_TEXT_BYTE_LIMIT // 4)
        self.assertEqual(
            SUMMARY.validate_plain_text(unicode_boundary, "field"),
            unicode_boundary,
        )
        with self.assertRaisesRegex(SUMMARY.SummaryError, "4096 UTF-8 bytes"):
            SUMMARY.validate_plain_text(unicode_boundary + "💥", "field")

    def test_review_comment_size_fallback_keeps_the_review_contract(self):
        boundary = "x" * SUMMARY.MODEL_TEXT_BYTE_LIMIT
        findings = [
            {
                "path": boundary,
                "line": line,
                "summary": boundary,
                "lane": "runeseer",
                "judgment": "confirmed",
                "severity": "medium",
            }
            for line in range(8)
        ]

        body = self.format_case(
            verdict(findings=findings),
            "**Request changes.** The inline findings contain the defects.",
        )

        self.assertLessEqual(SUMMARY.utf8_size(body), SUMMARY.MAX_GITHUB_COMMENT_BYTES)
        self.assertIn("<!-- runeseer-review -->", body)
        self.assertIn("### Runeseer review — 8 open findings", body)
        self.assertIn(SUMMARY.normalized_summary(verdict(findings=findings)), body)
        self.assertIn(SUMMARY.REVIEW_SIZE_FALLBACK, body)
        self.assertIn("[review run]", body)
        self.assertNotIn("| Risk |", body)

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
                "body": self.reviewdog_body(
                    "**Medium** — The setup omits its executable path."
                ),
            }
        ]
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.validate_verdict(verdict(), SHA, 1, None, comments)

    def test_reviewdog_v021_comment_matches_its_finding(self):
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
                "body": self.reviewdog_body(
                    "**Medium** — The setup omits its executable path."
                ),
            }
        ]
        self.assertEqual(SUMMARY.validate_verdict(data, SHA, 1, None, comments), data)

    def test_new_runeseer_comment_must_match_anchor_and_severity(self):
        for field, value in (
            ("line", 228),
            ("body", self.reviewdog_body("**High** — Wrong severity.", "🚫")),
        ):
            with self.subTest(field=field):
                finding = self.own_finding(comment_id=11)
                comment = self.posted_comment(11)
                comment[field] = value
                with self.assertRaises(SUMMARY.SummaryError):
                    SUMMARY.validate_verdict(
                        verdict(findings=[finding]), SHA, 1, None, [comment]
                    )

    def test_reviewdog_comment_needs_the_exact_runeseer_wrapper(self):
        body = self.reviewdog_body(
            "**Medium** — The setup omits its executable path."
        ).replace("**[runeseer]**", "**[other-tool]**")
        with self.assertRaisesRegex(SUMMARY.SummaryError, "exact Reviewdog tool"):
            SUMMARY.parse_reviewdog_comment(body)

    @staticmethod
    def reviewdog_body(message, icon="⚠️"):
        return (
            f"{icon} **[runeseer]** "
            "<sub>reported by [reviewdog]"
            "(https://github.com/reviewdog/reviewdog) :dog:</sub><br>"
            f"{message}\n"
            "<!-- __reviewdog__:ChBkMTAyNzkyYTU3MTg4ZWE0EgdydW5lc2Vlcg== -->\n"
        )

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
            "body": SummaryTests.reviewdog_body(
                "**Medium** — The setup omits its executable path."
            ),
        }

    @staticmethod
    def finding_record():
        return {
            "message": "**Medium** — The setup omits its executable path.",
            "location": {
                "path": "install-tools",
                "range": {"start": {"line": 227}},
            },
            "severity": "WARNING",
        }

    def test_binding_fills_the_comment_id_by_anchor(self):
        finding = self.own_finding()
        SUMMARY.validate_verdict(
            verdict(findings=[finding]), SHA, 1, None, [self.posted_comment(11)]
        )
        self.assertEqual(finding["comment_id"], 11)

    def test_format_persists_the_bound_comment_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            verdict_path = root / "verdict.json"
            summary_path = root / "summary.md"
            comments_path = root / "runeseer.json"
            verdict_path.write_text(
                json.dumps(verdict(findings=[self.own_finding()])), encoding="utf-8"
            )
            summary_path.write_text(
                "**Request changes.** The setup omits its executable path.",
                encoding="utf-8",
            )
            comments_path.write_text(
                json.dumps([self.posted_comment(11)]), encoding="utf-8"
            )

            SUMMARY.format_review(
                verdict_path,
                summary_path,
                SHA,
                1,
                RUN_URL,
                runeseer_comment_paths=[comments_path],
            )

            stored = json.loads(verdict_path.read_text(encoding="utf-8"))

        self.assertEqual(stored["findings"][0]["comment_id"], 11)

    def test_a_novel_finding_without_a_posted_comment_is_rejected(self):
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.validate_verdict(
                verdict(findings=[self.own_finding()]), SHA, 1, None, []
            )

    def test_filtered_finding_stays_blocking_without_an_inline_comment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            verdict_path = root / "verdict.json"
            summary_path = root / "summary.md"
            comments_path = root / "runeseer.json"
            findings_path = root / "runeseer-findings.rdjsonl"
            verdict_path.write_text(
                json.dumps(verdict(findings=[self.own_finding()])), encoding="utf-8"
            )
            summary_path.write_text(
                "**Request changes.** The setup omits its executable path.",
                encoding="utf-8",
            )
            comments_path.write_text("[]", encoding="utf-8")
            findings_path.write_text(
                json.dumps(self.finding_record()) + "\n", encoding="utf-8"
            )

            body = SUMMARY.format_review(
                verdict_path,
                summary_path,
                SHA,
                1,
                RUN_URL,
                runeseer_comment_paths=[comments_path],
                runeseer_findings_path=findings_path,
            )
            stored = json.loads(verdict_path.read_text(encoding="utf-8"))

        self.assertIn("### Runeseer review — 1 open finding", body)
        self.assertIsNone(stored["findings"][0]["comment_id"])

    def test_rdjson_record_byte_limit_accepts_boundary_and_rejects_overflow(self):
        record = json.dumps(self.finding_record(), separators=(",", ":"))
        self.assertLess(len(record.encode("utf-8")), SUMMARY.MAX_RDJSONL_RECORD_BYTES)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "findings.rdjsonl"
            boundary = record + " " * (
                SUMMARY.MAX_RDJSONL_RECORD_BYTES - len(record.encode("utf-8"))
            )
            path.write_text(boundary + "\n", encoding="utf-8")
            self.assertEqual(
                SUMMARY.load_runeseer_records(path), [self.finding_record()]
            )

            path.write_text(boundary + " \n", encoding="utf-8")
            with self.assertRaisesRegex(SUMMARY.SummaryError, "16384 UTF-8 bytes"):
                SUMMARY.load_runeseer_records(path)

    def test_rdjson_nested_text_uses_the_model_field_limit(self):
        record = self.finding_record()
        record["suggestions"] = [{"text": "x" * (SUMMARY.MODEL_TEXT_BYTE_LIMIT + 1)}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "findings.rdjsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(SUMMARY.SummaryError, "4096 UTF-8 bytes"):
                SUMMARY.load_runeseer_records(path)

    def test_an_ambiguous_anchor_binding_is_rejected(self):
        with self.assertRaises(SUMMARY.SummaryError):
            SUMMARY.validate_verdict(
                verdict(findings=[self.own_finding()]),
                SHA,
                1,
                None,
                [self.posted_comment(11), self.posted_comment(12)],
            )

    def test_a_carried_finding_rebinds_to_a_fresh_marked_comment(self):
        finding = self.own_finding(comment_id=999)
        SUMMARY.validate_verdict(
            verdict(findings=[finding]),
            SHA,
            1,
            None,
            [self.posted_comment(12)],
            [self.own_finding(comment_id=999)],
        )
        self.assertEqual(finding["comment_id"], 12)

    def test_a_carried_finding_preserves_its_prior_identity(self):
        previous = self.own_finding(comment_id=999)
        for field, value in (
            ("comment_id", None),
            ("path", "other-file"),
            ("line", 228),
            ("severity", "high"),
        ):
            with self.subTest(field=field):
                finding = self.own_finding(comment_id=999)
                finding[field] = value
                with self.assertRaises(SUMMARY.SummaryError):
                    SUMMARY.validate_verdict(
                        verdict(findings=[finding]), SHA, 1, None, [], [previous]
                    )

    def test_the_footer_carries_session_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "verdict.json").write_text(json.dumps(verdict()), encoding="utf-8")
            (root / "summary.md").write_text(
                "**Looks good.** Nothing blocks.", encoding="utf-8"
            )
            body = SUMMARY.format_review(
                root / "verdict.json",
                root / "summary.md",
                SHA,
                1,
                RUN_URL,
                session_stats="$1.23 · 1m35s · 24 turns",
            )
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


class WorkflowSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    @classmethod
    def section(cls, start, end):
        start_index = cls.source.index(start)
        end_index = cls.source.index(end, start_index)
        return cls.source[start_index:end_index]

    @classmethod
    def step(cls, marker):
        start_index = cls.source.index(marker)
        end_index = cls.source.find("\n            - ", start_index + len(marker))
        if end_index == -1:
            end_index = len(cls.source)
        return cls.source[start_index:end_index]

    def test_prevalidation_preparation_and_publication_are_split(self):
        markers = (
            "- id: prevalidate",
            "- id: post_findings",
            "- id: prepare",
            "- id: publish",
        )
        positions = [self.source.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        prevalidate = self.section("- id: prevalidate", "- id: post_findings")
        self.assertIn('python3 "$RUNESEER_FORMATTER" format', prevalidate)
        self.assertNotIn("reviewdog ", prevalidate)

    def test_label_consumption_is_scope_aware_and_verified(self):
        consume = self.section("- id: consume", "- id: restart")
        self.assertIn("SCOPE_SKIPPED: ${{ steps.scope.outputs.skip }}", consume)
        self.assertIn("BASE_RESET: ${{ steps.ledger.outputs.base_reset }}", consume)
        self.assertIn('[ "$BASE_RESET" != "true" ] || return 0', consume)
        self.assertIn("GH_TOKEN: ${{ github.token }}", consume)
        self.assertNotIn("steps.runeseer.outputs.token", consume)
        self.assertIn("if ! remaining=$(gh api --paginate", consume)
        self.assertIn("Could not verify the review label removal.", consume)
        self.assertIn("Could not consume the review label:", consume)

    def test_breaker_clears_transient_state_before_approval(self):
        breaker = self.section("- id: breaker", "# The spec's word is binding")
        cleanup = breaker.index("clear_blocker || clear_status=$?")
        approval = breaker.index("-f event=APPROVE")
        self.assertLess(cleanup, approval)
        approval_path = breaker[approval:]
        self.assertIn("record_failure workflow_after_publication", approval_path)
        self.assertIn("exit 1", approval_path)
        self.assertNotIn("- name: Approve on clean verdict", self.source)

    def test_owner_escalation_uses_the_canonical_findings_array(self):
        judgments = self.section("- id: judgments", "- id: upload")
        self.assertIn("confirmed=$(jq -c '.findings' runeseer-verdict.json)", judgments)

    def test_breaker_recovers_a_round_after_formatter_recovery(self):
        breaker = self.section("- id: breaker", "# The spec's word is binding")
        formatter = breaker.index("if ! ensure_formatter; then")
        fallback = breaker.index('case "$REVIEW_ROUND" in')
        self.assertLess(formatter, fallback)
        self.assertIn("marker-round", breaker)
        self.assertIn("REVIEW_ROUND=$((previous_round + 1))", breaker)

    def test_required_output_pair_is_verdict_and_findings(self):
        prevalidate = self.section("- id: prevalidate", "- id: post_findings")
        self.assertIn(
            "for output in runeseer-verdict.json runeseer-findings.rdjsonl; do",
            prevalidate,
        )
        breaker = self.section("- id: breaker", "# The spec's word is binding")
        required = breaker[
            breaker.index("required_outputs_exist=false") : breaker.index(
                "verdict_valid=false"
            )
        ]
        self.assertIn("runeseer-verdict.json", required)
        self.assertIn("runeseer-findings.rdjsonl", required)
        self.assertNotIn("runeseer-summary.md", required)

    def test_scope_skips_do_not_publish_a_notice(self):
        self.assertNotIn("Publish skipped review status", self.source)

    def test_base_reset_uses_guarded_cleanup_after_round_computation(self):
        ledger = self.section("- id: ledger", "- id: lanes")
        round_output = ledger.index('echo "round=${round}" >> "$GITHUB_OUTPUT"')
        cursor_clear = ledger.index('clear_stage "$LABEL_STAGE_CURSOR" || exit 1')
        macroscope_clear = ledger.index(
            'clear_stage "$LABEL_STAGE_MACROSCOPE" || exit 1'
        )
        self.assertLess(round_output, cursor_clear)
        self.assertLess(round_output, macroscope_clear)
        clear_helper = ledger[
            ledger.index("clear_stage() {") : ledger.index(
                'if [ "$base_reset" = "true" ]'
            )
        ]
        self.assertIn("check_current_round || return 1", clear_helper)

    def test_base_reset_stops_the_review_pipeline_before_adjudication(self):
        guard = "steps.ledger.outputs.base_reset != 'true'"
        guarded_steps = (
            "- id: lanes",
            "- id: runeseer",
            "- id: adjudicate",
            "- id: prevalidate",
            "- name: Install reviewdog",
            "- id: post_findings",
            "- id: fetch_findings",
            "- id: bind",
            "- id: prepare",
            "- id: publish",
            "- id: judgments",
            "- id: upload",
            "- id: restart",
        )
        for marker in guarded_steps:
            with self.subTest(step=marker):
                self.assertIn(guard, self.step(marker))

        breaker = self.step("- id: breaker")
        self.assertIn(guard, breaker)
        gate = self.section("# The spec's word is binding", "# Metrics:")
        self.assertIn("BASE_RESET: ${{ steps.ledger.outputs.base_reset }}", gate)
        self.assertIn(
            "The base-reset round stopped before Runeseer adjudication.", gate
        )

    def test_review_prompt_states_every_size_boundary(self):
        adjudicate = self.step("- id: adjudicate")
        for boundary in (
            "Keep each JSON record within 16,384 UTF-8 bytes.",
            "Keep each text field within 4,096 UTF-8 bytes.",
            "Keep the file within 4,096 UTF-8 bytes.",
            "Include at most 50 open findings and 200 lane judgments.",
            "Keep each model-authored text field within 4,096 UTF-8 bytes.",
        ):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, adjudicate)


class ReliabilityTests(unittest.TestCase):
    REPO = "runedeck/deck"
    PR = 49
    AUTHOR = "runeseer[bot]"
    RUN_ID = 123

    @staticmethod
    def completed(*, returncode=0, stdout="", stderr=""):
        return SUMMARY.subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    @staticmethod
    def classify(**overrides):
        state = {
            "verdict_exists": True,
            "required_outputs_exist": True,
            "verdict_valid": True,
            "formatter_outcome": "success",
            "adjudicate_outcome": "success",
            "prevalidate_outcome": "success",
            "prepare_outcome": "success",
            "publish_outcome": "success",
            "pre_breaker_status": "success",
            "judgments_outcome": "success",
            "upload_outcome": "success",
            "consume_outcome": "success",
            "restart_outcome": "success",
        }
        state.update(overrides)
        return SUMMARY.classify_failure(**state)

    @classmethod
    def notice_body(cls, notice_type, *, run_id=None, head=SHA, base=BASE):
        run_id = cls.RUN_ID if run_id is None else run_id
        if notice_type == "failure":
            return SUMMARY.failure_notice("invalid", RUN_URL, head, base, run_id)
        return SUMMARY.format_owner_escalation(
            "@N4M3Z",
            [
                {
                    "path": "runes/core/skills/IntakeIdea/SKILL.md",
                    "line": 46,
                    "summary": "Skill invokes an undocumented command",
                }
            ],
            head,
            base,
            run_id,
        )

    @classmethod
    def reconcile(cls, notice_type, body):
        function = (
            SUMMARY.reconcile_failure_notice
            if notice_type == "failure"
            else SUMMARY.reconcile_owner_escalation
        )
        return function(
            body,
            cls.REPO,
            cls.PR,
            cls.AUTHOR,
            SHA,
            BASE,
            3,
            cls.RUN_ID,
        )

    def test_repository_owner_uses_the_last_catch_all_rule(self):
        codeowners = """* @OldOwner
docs/** @DocsOwner
* @runedeck/maintainers @N4M3Z @SecondOwner"""
        self.assertEqual(SUMMARY.repository_owner(codeowners), "@N4M3Z")

    def test_repository_owner_rejects_missing_or_team_only_catch_all(self):
        for codeowners in ("docs/** @DocsOwner", "* @runedeck/maintainers"):
            with (
                self.subTest(codeowners=codeowners),
                self.assertRaises(SUMMARY.SummaryError),
            ):
                SUMMARY.repository_owner(codeowners)

    def test_size_contract_constants_are_exact(self):
        self.assertEqual(SUMMARY.MODEL_TEXT_BYTE_LIMIT, 4096)
        self.assertEqual(SUMMARY.MAX_OPEN_FINDINGS, 50)
        self.assertEqual(SUMMARY.MAX_LANE_JUDGMENTS, 200)
        self.assertEqual(SUMMARY.MAX_RDJSONL_RECORD_BYTES, 16384)
        self.assertEqual(SUMMARY.MAX_GITHUB_COMMENT_BYTES, 60000)

    def test_open_finding_limit_accepts_50_and_rejects_51(self):
        findings = [
            {
                "path": "file.py",
                "line": line,
                "summary": f"Finding {line}",
                "lane": "runeseer",
                "judgment": "confirmed",
                "severity": "medium",
            }
            for line in range(SUMMARY.MAX_OPEN_FINDINGS + 1)
        ]

        accepted = verdict(findings=findings[: SUMMARY.MAX_OPEN_FINDINGS])
        self.assertEqual(SUMMARY.validate_verdict(accepted, SHA, 1), accepted)
        with self.assertRaisesRegex(SUMMARY.SummaryError, "50 open findings"):
            SUMMARY.validate_verdict(verdict(findings=findings), SHA, 1)

    def test_lane_judgment_limit_accepts_200_and_rejects_201(self):
        judgments = [
            {
                "path": "file.py",
                "line": line,
                "summary": f"Judgment {line}",
                "lane": "runeseer",
                "judgment": "disputed",
                "severity": "low",
                "reason": "The reported defect is not present.",
                "comment_id": line + 1,
            }
            for line in range(SUMMARY.MAX_LANE_JUDGMENTS + 1)
        ]
        accepted = verdict()
        accepted["lane_judgments"] = judgments[: SUMMARY.MAX_LANE_JUDGMENTS]
        self.assertEqual(SUMMARY.validate_verdict(accepted, SHA, 1), accepted)

        rejected = verdict()
        rejected["lane_judgments"] = judgments
        with self.assertRaisesRegex(SUMMARY.SummaryError, "200 lane judgments"):
            SUMMARY.validate_verdict(rejected, SHA, 1)

    def test_owner_escalation_tags_the_owner_and_gives_complete_actions(self):
        findings = [
            {
                "path": "docs/decisions/DECK-0008 Idea-to-Merge Flywheel.md",
                "line": 16,
                "summary": "Decision record references an absent DECK entry",
                "escalation_key": "eyJsYW5lIjoicnVuZXNlZXIifQ==",
            },
            {
                "path": "runes/core/skills/IntakeIdea/SKILL.md",
                "line": 46,
                "summary": "Skill invokes an undocumented command",
            },
        ]

        body = SUMMARY.format_owner_escalation(
            "@N4M3Z", findings, SHA, BASE, self.RUN_ID
        )

        self.assertTrue(body.startswith("@N4M3Z,"))
        self.assertIn("Fix all findings.", body)
        self.assertIn("Then apply `review:runeseer`", body)
        self.assertIn("Apply `ignore:runeseer`", body)
        self.assertIn("`docs/decisions/DECK-0008 Idea-to-Merge Flywheel.md:16`", body)
        self.assertIn("<!-- runeseer-escalation:", body)
        self.assertEqual(body.count("<!-- runeseer-owner-escalation "), 1)
        self.assertIn(f"head={SHA} base={BASE} run={self.RUN_ID}", body)

    def test_owner_escalation_size_fallback_keeps_count_actions_and_marker(self):
        boundary = "x" * SUMMARY.MODEL_TEXT_BYTE_LIMIT
        findings = [
            {"path": boundary, "line": line, "summary": boundary} for line in range(8)
        ]

        body = SUMMARY.format_owner_escalation(
            "@N4M3Z", findings, SHA, BASE, self.RUN_ID
        )

        self.assertLessEqual(SUMMARY.utf8_size(body), SUMMARY.MAX_GITHUB_COMMENT_BYTES)
        self.assertTrue(body.startswith("@N4M3Z, 8 findings block this head:"))
        self.assertIn(SUMMARY.OWNER_SIZE_FALLBACK, body)
        self.assertIn(SUMMARY.OWNER_DETAILS_FALLBACK, body)
        for action in SUMMARY.OWNER_ACTIONS:
            self.assertIn(action, body)
        self.assertEqual(body.count("<!-- runeseer-owner-escalation "), 1)
        self.assertNotIn(boundary, body)

    def test_owner_escalation_rejects_more_than_50_findings(self):
        finding = {"path": "file.py", "line": 1, "summary": "Defect"}
        with self.assertRaisesRegex(SUMMARY.SummaryError, "50 findings"):
            SUMMARY.format_owner_escalation(
                "@N4M3Z",
                [finding] * (SUMMARY.MAX_OPEN_FINDINGS + 1),
                SHA,
                BASE,
                self.RUN_ID,
            )

    def test_complete_comment_limit_stops_remote_mutation(self):
        oversized_review = BODY + "x" * SUMMARY.MAX_GITHUB_COMMENT_BYTES
        with (
            patch.object(SUMMARY, "run_gh") as run_gh,
            self.assertRaisesRegex(SUMMARY.SummaryError, "60000 UTF-8 bytes"),
        ):
            SUMMARY.publish_summary(oversized_review, self.REPO, self.PR, self.AUTHOR)
        run_gh.assert_not_called()

        oversized_notice = (
            self.notice_body("failure") + "x" * SUMMARY.MAX_GITHUB_COMMENT_BYTES
        )
        with (
            patch.object(SUMMARY, "published_summary_is_newer") as newer,
            self.assertRaisesRegex(SUMMARY.SummaryError, "60000 UTF-8 bytes"),
        ):
            self.reconcile("failure", oversized_notice)
        newer.assert_not_called()

    def test_deck_49_validation_failure_is_not_a_provider_pause(self):
        kind = self.classify(
            verdict_valid=False,
            prevalidate_outcome="failure",
            prepare_outcome="skipped",
            publish_outcome="skipped",
            pre_breaker_status="failure",
            judgments_outcome="skipped",
            upload_outcome="skipped",
            restart_outcome="skipped",
        )

        self.assertEqual(kind, "invalid")
        body = SUMMARY.failure_notice(kind, RUN_URL, SHA, BASE, self.RUN_ID)
        self.assertIn("deterministic validation rejected it", body)
        self.assertNotIn("provider unavailable", body.lower())
        self.assertNotIn("paused", body.lower())

    def test_failure_classifier_covers_each_operational_phase(self):
        cases = (
            ("review action", {"adjudicate_outcome": "failure"}, "provider"),
            (
                "formatter",
                {
                    "formatter_outcome": "failure",
                    "adjudicate_outcome": "skipped",
                },
                "workflow",
            ),
            (
                "missing output",
                {"required_outputs_exist": False},
                "missing",
            ),
            (
                "prevalidation",
                {"prevalidate_outcome": "failure"},
                "invalid",
            ),
            ("preparation", {"prepare_outcome": "failure"}, "invalid"),
            ("publication", {"publish_outcome": "failure"}, "publication"),
            (
                "judgments",
                {
                    "judgments_outcome": "failure",
                    "pre_breaker_status": "failure",
                },
                "workflow_after_publication",
            ),
            (
                "upload",
                {
                    "upload_outcome": "failure",
                    "pre_breaker_status": "failure",
                },
                "workflow_after_publication",
            ),
            (
                "label consumption",
                {"consume_outcome": "failure"},
                "workflow_after_publication",
            ),
            (
                "restart",
                {
                    "restart_outcome": "failure",
                    "pre_breaker_status": "failure",
                },
                "workflow_after_publication",
            ),
        )
        for name, changes, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(self.classify(**changes), expected)

    def test_valid_findings_verdict_is_an_operational_success(self):
        self.assertIsNone(self.classify())

    @staticmethod
    def external_judgment(comment_id=5):
        return {
            "path": "file.py",
            "line": 12,
            "summary": "Guard accepts stale state",
            "lane": "cursor",
            "judgment": "confirmed",
            "severity": "medium",
            "reason": "The guard does not compare the live head.",
            "comment_id": comment_id,
        }

    @staticmethod
    def trusted_inline_comment(comment_id=5):
        return {
            "id": comment_id,
            "user": {"login": "cursor[bot]"},
            "path": "file.py",
            "line": 12,
            "in_reply_to_id": None,
        }

    def test_deck_49_mismatch_repairs_from_a_trusted_inline_judgment(self):
        data = verdict()
        data["lane_judgments"] = [self.external_judgment()]

        repaired = SUMMARY.canonicalize_external_findings(
            data, [self.trusted_inline_comment()]
        )

        self.assertEqual(data["findings"], [])
        self.assertEqual(repaired["count"], 1)
        self.assertEqual(repaired["verdict"], "findings")
        self.assertEqual(
            repaired["findings"],
            [
                {
                    "path": "file.py",
                    "line": 12,
                    "summary": "Guard accepts stale state",
                    "lane": "cursor",
                    "judgment": "confirmed",
                    "severity": "medium",
                    "comment_id": 5,
                }
            ],
        )
        self.assertEqual(
            SUMMARY.validate_verdict(repaired, SHA, 1, [self.trusted_inline_comment()]),
            repaired,
        )

    def test_external_finding_repair_discards_untrusted_model_entries(self):
        data = verdict()
        runeseer_finding = {
            "lane": "runeseer",
            "summary": "Preserve this object for verdict validation",
        }
        data["findings"] = [
            "malformed external finding",
            {"lane": "cursor", "comment_id": 999},
            {"lane": "unknown", "path": "stale.py"},
            runeseer_finding,
        ]
        data["lane_judgments"] = [self.external_judgment()]

        repaired = SUMMARY.canonicalize_external_findings(
            data, [self.trusted_inline_comment()]
        )

        self.assertEqual(repaired["findings"][0], runeseer_finding)
        self.assertIsNot(repaired["findings"][0], runeseer_finding)
        self.assertEqual(repaired["findings"][1]["comment_id"], 5)
        self.assertEqual(repaired["count"], 2)
        with self.assertRaisesRegex(SUMMARY.SummaryError, "blocking finding"):
            SUMMARY.validate_verdict(repaired, SHA, 1, [self.trusted_inline_comment()])

    def test_external_finding_repair_rejects_an_untrusted_comment_id(self):
        data = verdict()
        data["lane_judgments"] = [self.external_judgment(comment_id=999)]

        with self.assertRaisesRegex(SUMMARY.SummaryError, "fetched lane comment"):
            SUMMARY.canonicalize_external_findings(
                data, [self.trusted_inline_comment()]
            )

    def test_external_finding_repair_rejects_ambiguous_source_identity(self):
        data = verdict()
        data["lane_judgments"] = [self.external_judgment()]
        comments = [self.trusted_inline_comment(), self.trusted_inline_comment()]

        with self.assertRaisesRegex(SUMMARY.SummaryError, "ambiguous"):
            SUMMARY.canonicalize_external_findings(data, comments)

    def test_external_finding_repair_rejects_an_issue_comment_identity(self):
        data = verdict()
        data["lane_judgments"] = [self.external_judgment()]
        issue_comment = {
            "id": 5,
            "user": {"login": "cursor[bot]"},
            "in_reply_to_id": None,
        }

        with self.assertRaisesRegex(SUMMARY.SummaryError, "root inline comment"):
            SUMMARY.canonicalize_external_findings(data, [issue_comment])

    def test_failure_classifier_handles_cancellation_without_claiming_an_outage(self):
        self.assertIsNone(self.classify(adjudicate_outcome="cancelled"))
        self.assertIsNone(self.classify(pre_breaker_status="cancelled"))

    def test_failure_notices_never_claim_a_generic_provider_outage(self):
        for kind in SUMMARY.FAILURE_NOTICES:
            with self.subTest(kind=kind):
                body = SUMMARY.failure_notice(kind, RUN_URL, SHA, BASE, self.RUN_ID)
                self.assertNotIn("provider unavailable", body.lower())
                self.assertNotIn("provider paused", body.lower())
                self.assertIn(f"stage={kind}", body)

    @patch.object(SUMMARY, "run_gh")
    def test_live_pull_request_rejects_head_base_and_api_faults(self, run_gh):
        cases = (
            (self.completed(stdout=f"{'f' * 40}\t{BASE}\n"), "head"),
            (self.completed(stdout=f"{SHA}\t{'f' * 40}\n"), "base"),
            (self.completed(returncode=1, stderr="API failed"), "API"),
        )
        for result, name in cases:
            with self.subTest(name=name):
                run_gh.return_value = result
                with self.assertRaises(SUMMARY.SummaryError):
                    SUMMARY.verify_live_pull_request(self.REPO, self.PR, SHA, BASE)

    def test_notice_marker_mismatch_stops_before_remote_reads(self):
        bodies = (
            self.notice_body("failure", head="f" * 40),
            self.notice_body("failure", base="f" * 40),
            self.notice_body("failure", run_id=self.RUN_ID + 1),
        )
        with patch.object(SUMMARY, "published_summary_is_newer") as newer:
            for body in bodies:
                with (
                    self.subTest(body=body.rsplit("\n", 1)[-1]),
                    self.assertRaises(SUMMARY.SummaryError),
                ):
                    self.reconcile("failure", body)
            newer.assert_not_called()

    def test_newer_published_run_stops_notice_mutation(self):
        body = self.notice_body("failure")
        with (
            patch.object(SUMMARY, "published_summary_is_newer", return_value=True),
            patch.object(SUMMARY, "find_transient_comments") as find_comments,
            patch.object(SUMMARY, "run_gh") as run_gh,
        ):
            action = self.reconcile("failure", body)

        self.assertEqual(action, "kept newer")
        find_comments.assert_not_called()
        run_gh.assert_not_called()

    def test_newer_sticky_notice_stops_notice_mutation(self):
        body = self.notice_body("failure")
        newer_comment = {
            "id": 9,
            "_runeseer_transient_run": self.RUN_ID + 1,
        }
        with (
            patch.object(SUMMARY, "published_summary_is_newer", return_value=False),
            patch.object(
                SUMMARY, "find_transient_comments", return_value=[newer_comment]
            ),
            patch.object(SUMMARY, "run_gh") as run_gh,
        ):
            action = self.reconcile("failure", body)

        self.assertEqual(action, "kept newer")
        run_gh.assert_not_called()

    def test_latest_summary_clock_rejects_other_heads_and_bases(self):
        current = (
            BODY.replace("round=1", "round=3")
            + "\n\n---\nNo open findings · Reviewed `01234567` · "
            + "[review run](https://github.com/runedeck/deck/actions/runs/124)"
        )
        other_head = current.replace(SHA, "f" * 40).replace("runs/124", "runs/999")
        other_base = current.replace(BASE, "e" * 40).replace("runs/124", "runs/998")
        comments = (
            {"id": 1, "user": {"login": self.AUTHOR}, "body": other_head},
            {"id": 2, "user": {"login": self.AUTHOR}, "body": other_base},
            {"id": 3, "user": {"login": self.AUTHOR}, "body": current},
        )
        result = self.completed(
            stdout="\n".join(json.dumps(comment) for comment in comments)
        )
        with patch.object(SUMMARY, "run_gh", return_value=result):
            self.assertEqual(
                SUMMARY.latest_summary_clock(
                    self.REPO, self.PR, self.AUTHOR, SHA, BASE
                ),
                (3, 124),
            )
            self.assertTrue(
                SUMMARY.published_summary_is_newer(
                    self.REPO, self.PR, self.AUTHOR, SHA, BASE, 3, 123
                )
            )

    def test_latest_summary_clock_ignores_a_model_authored_run_link(self):
        poisoned = (
            BODY.replace("round=1", "round=3").replace(
                "**Looks good.** The change is correct.",
                "**Looks good.** [review run](https://github.com/runedeck/deck/actions/runs/999999) is model prose.",
            )
            + "\n\n---\nNo open findings · Reviewed `01234567` · "
            + "[review run](https://github.com/runedeck/deck/actions/runs/124)"
        )
        comment = {"id": 3, "user": {"login": self.AUTHOR}, "body": poisoned}
        result = self.completed(stdout=json.dumps(comment))

        with patch.object(SUMMARY, "run_gh", return_value=result):
            self.assertEqual(
                SUMMARY.latest_summary_clock(
                    self.REPO, self.PR, self.AUTHOR, SHA, BASE
                ),
                (3, 124),
            )

    def test_transient_comment_reads_fail_closed(self):
        failure = self.completed(returncode=1, stderr="API failed")
        with patch.object(SUMMARY, "run_gh", return_value=failure):
            with self.assertRaises(SUMMARY.SummaryError):
                SUMMARY.find_transient_comments(
                    self.REPO, self.PR, self.AUTHOR, "failure"
                )
            with self.assertRaises(SUMMARY.SummaryError):
                SUMMARY.read_transient_comment(self.REPO, 7, self.AUTHOR, "failure")

    def test_sticky_notices_create(self):
        for notice_type in ("failure", "owner"):
            body = self.notice_body(notice_type)
            response = self.completed(stdout=json.dumps({"id": 7, "body": body}))
            with (
                self.subTest(notice_type=notice_type),
                patch.object(SUMMARY, "published_summary_is_newer", return_value=False),
                patch.object(SUMMARY, "find_transient_comments", return_value=[]),
                patch.object(SUMMARY, "verify_live_pull_request") as verify,
                patch.object(SUMMARY, "run_gh", return_value=response) as run_gh,
            ):
                action = self.reconcile(notice_type, body)

                self.assertEqual(action, "created")
                verify.assert_called_once_with(self.REPO, self.PR, SHA, BASE)
                arguments = run_gh.call_args.args[0]
                self.assertEqual(arguments[:3], ["api", "-X", "POST"])
                self.assertIn(f"issues/{self.PR}/comments", arguments[3])

    def test_sticky_notices_update(self):
        for notice_type in ("failure", "owner"):
            body = self.notice_body(notice_type)
            current = {
                "id": 7,
                "_runeseer_transient_run": self.RUN_ID - 1,
                "updated_at": "2026-08-26T01:00:00Z",
            }
            response = self.completed(stdout=json.dumps({"id": 7, "body": body}))
            with (
                self.subTest(notice_type=notice_type),
                patch.object(SUMMARY, "published_summary_is_newer", return_value=False),
                patch.object(
                    SUMMARY, "find_transient_comments", return_value=[current]
                ),
                patch.object(SUMMARY, "read_transient_comment", return_value=current),
                patch.object(SUMMARY, "verify_live_pull_request") as verify,
                patch.object(SUMMARY, "run_gh", return_value=response) as run_gh,
            ):
                action = self.reconcile(notice_type, body)

                self.assertEqual(action, "updated")
                verify.assert_called_once_with(self.REPO, self.PR, SHA, BASE)
                arguments = run_gh.call_args.args[0]
                self.assertEqual(arguments[:3], ["api", "-X", "PATCH"])
                self.assertIn("issues/comments/7", arguments[3])

    def test_sticky_notices_clear(self):
        for notice_type in ("failure", "owner"):
            current = {
                "id": 7,
                "_runeseer_transient_run": self.RUN_ID - 1,
            }
            with (
                self.subTest(notice_type=notice_type),
                patch.object(SUMMARY, "published_summary_is_newer", return_value=False),
                patch.object(
                    SUMMARY, "find_transient_comments", return_value=[current]
                ),
                patch.object(SUMMARY, "read_transient_comment", return_value=current),
                patch.object(SUMMARY, "verify_live_pull_request") as verify,
                patch.object(
                    SUMMARY, "run_gh", return_value=self.completed()
                ) as run_gh,
            ):
                action = self.reconcile(notice_type, None)

                self.assertEqual(action, "removed 1")
                verify.assert_called_once_with(self.REPO, self.PR, SHA, BASE)
                arguments = run_gh.call_args.args[0]
                self.assertEqual(arguments[:3], ["api", "-X", "DELETE"])
                self.assertIn("issues/comments/7", arguments[3])

    def test_failed_notice_clear_stops_clean_recovery(self):
        current = {
            "id": 7,
            "_runeseer_transient_run": self.RUN_ID - 1,
        }
        failure = self.completed(returncode=1, stderr="delete failed")
        with (
            patch.object(SUMMARY, "published_summary_is_newer", return_value=False),
            patch.object(SUMMARY, "find_transient_comments", return_value=[current]),
            patch.object(SUMMARY, "read_transient_comment", return_value=current),
            patch.object(SUMMARY, "verify_live_pull_request"),
            patch.object(SUMMARY, "run_gh", return_value=failure),
            self.assertRaisesRegex(SUMMARY.SummaryError, "remove the stale"),
        ):
            self.reconcile("failure", None)

    def test_legacy_notices_are_migrated_and_deduplicated(self):
        cases = (
            (
                "failure",
                "Runeseer paused.\n<!-- runeseer-provider-failure:" + SHA + " -->",
            ),
            (
                "owner",
                f"{SUMMARY.LEGACY_OWNER_ESCALATION_PREFIX}\n- `file.md:1`: Legacy decision",
            ),
        )
        for notice_type, legacy_body in cases:
            body = self.notice_body(notice_type)
            older = {
                "id": 6,
                "user": {"login": self.AUTHOR},
                "body": legacy_body,
                "_runeseer_transient_run": 0,
                "updated_at": "2026-08-26T01:00:00Z",
            }
            current = {
                "id": 7,
                "user": {"login": self.AUTHOR},
                "body": legacy_body,
                "_runeseer_transient_run": 0,
                "updated_at": "2026-08-26T02:00:00Z",
            }
            response = self.completed(stdout=json.dumps({"id": 7, "body": body}))
            with (
                self.subTest(notice_type=notice_type),
                patch.object(SUMMARY, "published_summary_is_newer", return_value=False),
                patch.object(
                    SUMMARY, "find_transient_comments", return_value=[older, current]
                ),
                patch.object(
                    SUMMARY,
                    "read_transient_comment",
                    side_effect=[current, older],
                ),
                patch.object(SUMMARY, "verify_live_pull_request") as verify,
                patch.object(
                    SUMMARY,
                    "run_gh",
                    side_effect=[response, self.completed()],
                ) as run_gh,
            ):
                action = self.reconcile(notice_type, body)

                self.assertEqual(action, "updated")
                self.assertEqual(verify.call_count, 2)
                self.assertIn("issues/comments/7", run_gh.call_args_list[0].args[0][3])
                self.assertIn("issues/comments/6", run_gh.call_args_list[1].args[0][3])

    @patch.object(SUMMARY, "run_gh")
    def test_transient_comment_listing_filters_type_and_author(self, run_gh):
        failure_body = self.notice_body("failure")
        owner_body = self.notice_body("owner")
        comments = (
            {"id": 1, "user": {"login": self.AUTHOR}, "body": failure_body},
            {"id": 2, "user": {"login": "attacker"}, "body": failure_body},
            {"id": 3, "user": {"login": self.AUTHOR}, "body": owner_body},
        )
        run_gh.return_value = self.completed(
            stdout="\n".join(json.dumps(comment) for comment in comments)
        )

        found = SUMMARY.find_transient_comments(
            self.REPO, self.PR, self.AUTHOR, "failure"
        )

        self.assertEqual([comment["id"] for comment in found], [1])
        self.assertEqual(found[0]["_runeseer_transient_run"], self.RUN_ID)

    @patch.object(SUMMARY, "run_gh")
    def test_transient_comment_reread_revalidates_identity(self, run_gh):
        body = self.notice_body("failure")
        owned = {"id": 7, "user": {"login": self.AUTHOR}, "body": body}
        replaced = {"id": 7, "user": {"login": "attacker"}, "body": body}
        run_gh.side_effect = [
            self.completed(stdout=json.dumps(owned)),
            self.completed(stdout=json.dumps(replaced)),
        ]

        comment = SUMMARY.read_transient_comment(self.REPO, 7, self.AUTHOR, "failure")
        self.assertEqual(comment["_runeseer_transient_run"], self.RUN_ID)
        with self.assertRaisesRegex(SUMMARY.SummaryError, "no longer"):
            SUMMARY.read_transient_comment(self.REPO, 7, self.AUTHOR, "failure")

    def test_transient_notice_type_is_validated(self):
        with self.assertRaisesRegex(SUMMARY.SummaryError, "type is invalid"):
            SUMMARY.transient_notice_spec("unknown")

    def test_legacy_markers_are_bot_owned_and_type_specific(self):
        cases = (
            (
                "failure",
                "<!-- runeseer-provider-failure:" + SHA + " -->",
            ),
            ("owner", "<!-- runeseer-escalation:YWJj -->"),
        )
        for notice_type, body in cases:
            with self.subTest(notice_type=notice_type):
                parsed = SUMMARY.parse_transient_comment(
                    {
                        "id": 7,
                        "user": {"login": self.AUTHOR},
                        "body": body,
                    },
                    self.AUTHOR,
                    notice_type,
                )
                self.assertEqual(parsed["_runeseer_transient_run"], 0)
                self.assertIsNone(
                    SUMMARY.parse_transient_comment(
                        {
                            "id": 8,
                            "user": {"login": "attacker"},
                            "body": body,
                        },
                        self.AUTHOR,
                        notice_type,
                    )
                )


if __name__ == "__main__":
    unittest.main()
