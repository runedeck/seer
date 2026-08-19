#!/usr/bin/env python3
"""Validate, format, and publish the current Runeseer review summary."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SUMMARY_MARKER = "<!-- runeseer-review -->"
VERDICT_MARKER = (
    "<!-- runeseer-verdict sha={sha} base={base} round={round} "
    "verdict={verdict} restart={restart} -->"
)
VERDICT_MARKER_RE = re.compile(
    r"<!-- runeseer-verdict sha=(?P<sha>[0-9a-f]{40}) "
    r"base=(?P<base>[0-9a-f]{40}) round=(?P<round>[0-9]+) "
    r"verdict=(?P<verdict>clean|findings)"
    r"(?: restart=(?P<restart>none|cursor|macroscope))? -->"
)
VERDICTS = {"clean", "findings"}
RESTARTS = {"none", "cursor", "macroscope"}
LANE_LOGINS = {
    "cursor[bot]": "cursor",
    "macroscopeapp[bot]": "macroscope",
}
PROHIBITED_LINES = (
    "lane judgments",
    "digest",
    "carried",
    "confirmed",
    "disputed",
    "already addressed",
)
VERDICT_PREFIXES = {
    "clean": "**Looks good.**",
    "findings": "**Request changes.**",
}


class SummaryError(RuntimeError):
    """Report invalid review state or a publication failure."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SummaryError(f"Could not read valid JSON from {path}: {error}") from error


def load_lane_comments(paths: list[Path] | None) -> list[dict[str, Any]] | None:
    if paths is None:
        return None
    comments: list[dict[str, Any]] = []
    for path in paths:
        value = load_json(path)
        if not isinstance(value, list) or not all(
            isinstance(comment, dict) for comment in value
        ):
            raise SummaryError(f"The lane comment file must contain an array: {path}")
        comments.extend(value)
    return comments


def load_previous_findings(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    value = load_json(path)
    findings = value.get("findings") if isinstance(value, dict) else None
    if not isinstance(findings, list) or not all(
        isinstance(finding, dict) for finding in findings
    ):
        raise SummaryError("The previous verdict has no valid findings array.")
    return findings


def validate_lane_bindings(
    judgments: list[dict[str, Any]],
    lane_comments: list[dict[str, Any]],
    nonfinding_issue_ids: list[int],
) -> None:
    sources: dict[int, tuple[str, dict[str, Any]]] = {}
    required_inline_ids: set[int] = set()
    issue_ids: set[int] = set()
    for comment in lane_comments:
        comment_id = comment.get("id")
        user = comment.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        lane = LANE_LOGINS.get(login)
        if type(comment_id) is not int or lane is None:
            continue
        sources[comment_id] = (lane, comment)
        if isinstance(comment.get("path"), str):
            if comment.get("in_reply_to_id") is None:
                required_inline_ids.add(comment_id)
        else:
            issue_ids.add(comment_id)

    judged_ids: set[int] = set()
    for judgment in judgments:
        # Ledger rechecks judge Runeseer's own earlier findings. Those
        # comments live outside the external lane files, so the external
        # binding rules below cannot apply to them.
        if judgment.get("lane") == "runeseer":
            continue
        comment_id = judgment["comment_id"]
        if comment_id in judged_ids:
            raise SummaryError("Each lane comment can have only one judgment.")
        source = sources.get(comment_id)
        if source is None:
            raise SummaryError(
                "Each lane judgment comment ID must identify a fetched lane comment."
            )
        source_lane, comment = source
        if judgment["lane"] != source_lane:
            raise SummaryError("Each lane judgment must preserve its source lane.")
        source_path = comment.get("path")
        source_line = comment.get("line") or comment.get("original_line")
        if isinstance(source_path, str) and judgment["path"] != source_path:
            raise SummaryError("Each inline judgment must preserve its source path.")
        if type(source_line) is int and judgment["line"] != source_line:
            raise SummaryError("Each inline judgment must preserve its source line.")
        judged_ids.add(comment_id)

    missing = required_inline_ids - judged_ids
    if missing:
        raise SummaryError("Every fetched lane inline finding needs a lane judgment.")

    nonfinding = set(nonfinding_issue_ids)
    judged_issues = judged_ids & issue_ids
    if len(nonfinding) != len(nonfinding_issue_ids) or nonfinding & judged_issues:
        raise SummaryError("Each lane issue comment needs one classification.")
    if nonfinding | judged_issues != issue_ids:
        raise SummaryError(
            "Every lane issue comment needs a judgment or no-finding status."
        )


def validate_runeseer_bindings(
    findings: list[dict[str, Any]],
    comments: list[dict[str, Any]],
    previous_findings: list[dict[str, Any]],
) -> None:
    sources: dict[int, dict[str, Any]] = {}
    for comment in comments:
        comment_id = comment.get("id")
        if type(comment_id) is int and comment.get("in_reply_to_id") is None:
            sources[comment_id] = comment

    own_findings = [
        finding for finding in findings if finding.get("lane") == "runeseer"
    ]
    finding_ids = {
        finding["comment_id"]
        for finding in own_findings
        if finding.get("comment_id") is not None
    }
    missing = set(sources) - finding_ids
    if missing:
        raise SummaryError("Every new Runeseer inline comment needs an open finding.")

    previous_own = [
        finding for finding in previous_findings if finding.get("lane") == "runeseer"
    ]
    previous_ids = {
        finding.get("comment_id")
        for finding in previous_own
        if type(finding.get("comment_id")) is int
    }
    previous_keys = {
        (finding.get("path"), finding.get("line"), finding.get("summary"))
        for finding in previous_own
    }
    for finding in own_findings:
        comment_id = finding.get("comment_id")
        key = (finding.get("path"), finding.get("line"), finding.get("summary"))
        if comment_id is None and key not in previous_keys:
            raise SummaryError("Each new Runeseer finding needs its inline comment ID.")
        if (
            comment_id is not None
            and comment_id not in sources
            and comment_id not in previous_ids
        ):
            raise SummaryError(
                "Each Runeseer finding ID needs new or carried evidence."
            )

    severity_pattern = re.compile(r"^\*\*(Critical|High|Medium)\*\*")
    for comment_id in set(sources) & finding_ids:
        comment = sources[comment_id]
        finding = next(
            item for item in findings if item.get("comment_id") == comment_id
        )
        source_line = comment.get("line") or comment.get("original_line")
        if finding["path"] != comment.get("path") or finding["line"] != source_line:
            raise SummaryError("Each Runeseer finding must preserve its inline anchor.")
        severity = severity_pattern.match(comment.get("body", ""))
        if severity is None or finding["severity"] != severity.group(1).lower():
            raise SummaryError(
                "Each Runeseer finding must preserve its inline severity."
            )


def validate_plain_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SummaryError(f"{field} must be a nonempty string.")
    if "\n" in value or "\r" in value or "<!--" in value or "-->" in value:
        raise SummaryError(f"{field} must be one plain-text line.")
    return value


def validate_verdict(
    verdict: Any,
    expected_sha: str,
    expected_round: int,
    lane_comments: list[dict[str, Any]] | None = None,
    runeseer_comments: list[dict[str, Any]] | None = None,
    previous_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(verdict, dict):
        raise SummaryError("The verdict must be a JSON object.")
    if verdict.get("sha") != expected_sha:
        raise SummaryError("The verdict SHA does not match the reviewed head.")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise SummaryError(
            "The reviewed SHA must contain 40 lowercase hexadecimal characters."
        )
    if verdict.get("verdict") not in VERDICTS:
        raise SummaryError("The verdict value must be clean or findings.")
    if verdict.get("restart") not in RESTARTS:
        raise SummaryError("The restart value is invalid.")
    if type(expected_round) is not int or expected_round < 1:
        raise SummaryError("The review round must be a positive integer.")
    if type(verdict.get("round")) is not int or verdict.get("round") != expected_round:
        raise SummaryError("The verdict round does not match the current round.")

    if not re.fullmatch(r"[0-9a-f]{40}", verdict.get("base", "")):
        raise SummaryError(
            "The verdict base must contain 40 lowercase hexadecimal characters."
        )

    findings = verdict.get("findings")
    judgments = verdict.get("lane_judgments")
    nonfinding_issue_ids = verdict.get("nonfinding_issue_comment_ids")
    if not isinstance(findings, list):
        raise SummaryError("The findings field must be an array.")
    if not isinstance(judgments, list):
        raise SummaryError("The lane_judgments field must be an array.")
    if not isinstance(nonfinding_issue_ids, list) or any(
        type(comment_id) is not int or comment_id < 1
        for comment_id in nonfinding_issue_ids
    ):
        raise SummaryError(
            "The reviewed issue comment IDs must be positive integer values."
        )
    count = verdict.get("count")
    if type(count) is not int or count < 0 or count != len(findings):
        raise SummaryError(
            "The finding count must be a nonnegative integer equal to the findings array length."
        )
    for item in findings:
        if not isinstance(item, dict):
            raise SummaryError("Each finding must be an object.")
        if item.get("severity") not in {"medium", "high", "critical"}:
            raise SummaryError(
                "Each blocking finding needs Medium, High, or Critical severity."
            )
        validate_plain_text(item.get("path"), "Each finding path")
        if type(item.get("line")) is not int or item["line"] < 0:
            raise SummaryError("Each finding needs a nonnegative line number.")
        validate_plain_text(item.get("summary"), "Each finding summary")
        if item.get("lane") not in {"runeseer", "cursor", "macroscope"}:
            raise SummaryError("Each finding needs a known source lane.")
        if item.get("judgment") != "confirmed":
            raise SummaryError("Each open finding needs a confirmed judgment.")
        comment_id = item.get("comment_id")
        if comment_id is not None and (type(comment_id) is not int or comment_id < 1):
            raise SummaryError("Each finding comment ID must be a positive integer.")
        if item["lane"] != "runeseer" and comment_id is None:
            raise SummaryError("Each lane finding needs its source comment ID.")
    for item in judgments:
        if not isinstance(item, dict):
            raise SummaryError("Each lane judgment must be an object.")
        if item.get("judgment") not in {"confirmed", "disputed", "already addressed"}:
            raise SummaryError("Each lane judgment has an invalid judgment value.")
        if item.get("severity") not in {"low", "medium", "high", "critical"}:
            raise SummaryError("Each lane judgment has an invalid severity value.")
        validate_plain_text(item.get("path"), "Each lane judgment path")
        if type(item.get("line")) is not int or item["line"] < 0:
            raise SummaryError("Each lane judgment needs a nonnegative line number.")
        validate_plain_text(item.get("summary"), "Each lane judgment summary")
        if item.get("lane") not in set(LANE_LOGINS.values()) | {"runeseer"}:
            raise SummaryError("Each lane judgment needs a known source lane.")
        comment_id = item.get("comment_id")
        if type(comment_id) is not int or comment_id < 1:
            raise SummaryError(
                "Each lane judgment comment ID must be a positive integer."
            )
        validate_plain_text(item.get("reason"), "Each lane judgment reason")
    if lane_comments is not None:
        validate_lane_bindings(judgments, lane_comments, nonfinding_issue_ids)
    if runeseer_comments is not None:
        validate_runeseer_bindings(findings, runeseer_comments, previous_findings or [])
    finding_keys = [
        (item.get("lane"), item.get("path"), item.get("line"), item.get("comment_id"))
        for item in findings
    ]
    if len(finding_keys) != len(set(finding_keys)):
        raise SummaryError("Each open finding needs a unique identity.")
    lane_findings = {
        key: item
        for key, item in zip(finding_keys, findings, strict=True)
        if key[0] != "runeseer"
    }
    confirmed = {
        (
            item.get("lane"),
            item.get("path"),
            item.get("line"),
            item.get("comment_id"),
        ): item
        for item in judgments
        if item.get("judgment") == "confirmed"
        and item.get("severity") != "low"
        and item.get("lane") != "runeseer"
    }
    if confirmed.keys() != lane_findings.keys():
        raise SummaryError(
            "Confirmed lane judgments and open lane findings must match exactly."
        )
    for key, finding in lane_findings.items():
        judgment = confirmed[key]
        if (
            finding["summary"] != judgment["summary"]
            or finding["severity"] != judgment["severity"]
        ):
            raise SummaryError(
                "Each lane finding must preserve its judgment summary and severity."
            )
    expected_verdict = "clean" if not findings else "findings"
    if verdict["verdict"] != expected_verdict:
        raise SummaryError("The verdict value does not match the findings array.")
    return verdict


def prose_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9_'’./:-]*", text))


def validate_summary(summary: str, verdict: dict[str, Any]) -> str:
    summary = summary.strip()
    if not summary:
        raise SummaryError("The review summary is empty.")
    if SUMMARY_MARKER in summary or "<!-- runeseer-verdict" in summary:
        raise SummaryError("The model summary must not contain workflow markers.")
    if "\n---" in summary or any(line.startswith("#") for line in summary.splitlines()):
        raise SummaryError("The model summary must not contain headings or a footer.")

    for line in summary.splitlines():
        normalized = line.strip().strip("#*- ").lower().rstrip(":")
        if normalized in PROHIBITED_LINES:
            raise SummaryError(
                f"The review summary contains the internal heading: {normalized}"
            )
        if line.strip().lower().startswith("review/correctness:"):
            raise SummaryError("The review summary contains the internal machine key.")

    first_line = next((line for line in summary.splitlines() if line.strip()), "")
    state = (
        "findings" if verdict["findings"] or verdict["restart"] != "none" else "clean"
    )
    prefix = VERDICT_PREFIXES[state]
    if not first_line.startswith(prefix + " "):
        raise SummaryError(f"The first line must start with {prefix}")
    if len(first_line.removeprefix(prefix).strip()) < 2:
        raise SummaryError("The first line must include a concrete review sentence.")

    bullet_count = sum(
        1 for line in summary.splitlines() if line.lstrip().startswith("- ")
    )
    if bullet_count > 3:
        raise SummaryError("The review summary must contain at most three bullets.")
    if prose_word_count(summary) > 80:
        raise SummaryError("The review summary must contain at most 80 words.")
    return summary


def open_count_text(count: int) -> str:
    if count == 0:
        return "No open findings"
    if count == 1:
        return "1 open"
    return f"{count} open"


def format_review(
    verdict_path: Path,
    summary_path: Path,
    expected_sha: str,
    expected_round: int,
    run_url: str,
    lane_comment_paths: list[Path] | None = None,
    runeseer_comment_paths: list[Path] | None = None,
    previous_verdict_path: Path | None = None,
) -> str:
    verdict = validate_verdict(
        load_json(verdict_path),
        expected_sha,
        expected_round,
        load_lane_comments(lane_comment_paths),
        load_lane_comments(runeseer_comment_paths),
        load_previous_findings(previous_verdict_path),
    )
    try:
        summary_text = summary_path.read_text(encoding="utf-8")
    except OSError as error:
        raise SummaryError(f"Could not read {summary_path}: {error}") from error
    summary = validate_summary(summary_text, verdict)
    footer = " · ".join(
        (
            open_count_text(len(verdict["findings"])),
            f"Reviewed `{expected_sha[:8]}`",
            f"[review run]({run_url})",
        )
    )
    return "\n".join(
        (
            SUMMARY_MARKER,
            VERDICT_MARKER.format(
                sha=expected_sha,
                base=verdict["base"],
                round=verdict["round"],
                verdict=verdict["verdict"],
                restart=verdict["restart"],
            ),
            summary,
            "",
            "---",
            footer,
        )
    )


def next_round(
    previous_verdict_path: Path | None, legacy_rounds: int, marker_round: int
) -> int:
    previous_round = 0
    if previous_verdict_path and previous_verdict_path.exists():
        previous = load_json(previous_verdict_path)
        value = previous.get("round", 0) if isinstance(previous, dict) else 0
        if type(value) is not int or value < 0:
            raise SummaryError(
                "The previous verdict round must be a nonnegative integer."
            )
        previous_round = value
    if legacy_rounds < 0 or marker_round < 0:
        raise SummaryError("The earlier round counts must be nonnegative.")
    return max(previous_round, legacy_rounds, marker_round) + 1


def run_gh(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def parse_verdict_marker(body: str) -> dict[str, Any] | None:
    match = VERDICT_MARKER_RE.search(body)
    if match is None:
        return None
    return {
        "sha": match.group("sha"),
        "base": match.group("base"),
        "round": int(match.group("round")),
        "verdict": match.group("verdict"),
        "restart": match.group("restart") or "none",
    }


def marker_round(body: str, base: str | None = None) -> int:
    rounds = [
        int(match.group("round"))
        for match in VERDICT_MARKER_RE.finditer(body)
        if base is None or match.group("base") == base
    ]
    return max(rounds, default=0)


def find_summary_comment(
    repo: str, number: int, author: str, base: str | None = None
) -> dict[str, Any] | None:
    endpoint = f"repos/{repo}/issues/{number}/comments?per_page=100&sort=created&direction=desc"
    result = run_gh(["api", "--paginate", endpoint, "--jq", ".[] | @json"])
    if result.returncode != 0:
        raise SummaryError(
            f"Could not read pull request comments: {result.stderr.strip()}"
        )
    comments = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            comment = json.loads(line)
        except json.JSONDecodeError as error:
            raise SummaryError("GitHub returned an invalid comment record.") from error
        user = comment.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        if login == author and SUMMARY_MARKER in comment.get("body", ""):
            comments.append(comment)
    if not comments:
        return None
    same_base = [
        comment
        for comment in comments
        if base is not None
        and (marker := parse_verdict_marker(comment.get("body", ""))) is not None
        and marker["base"] == base
    ]
    candidates = same_base or comments
    current = max(
        candidates,
        key=lambda comment: (
            marker_round(comment.get("body", ""), base),
            comment.get("updated_at", comment.get("created_at", "")),
            comment.get("id", 0),
        ),
    )
    if not isinstance(current.get("id"), int):
        raise SummaryError("The existing summary comment has no numeric ID.")
    return current


def publish_summary(body: str, repo: str, number: int, author: str) -> str:
    marker = parse_verdict_marker(body)
    if marker is None or marker["round"] < 1:
        raise SummaryError("The review summary has no valid verdict marker.")
    comment = find_summary_comment(repo, number, author, marker["base"])
    comment_id = comment.get("id") if comment else None
    existing_round = (
        marker_round(comment.get("body", ""), marker["base"]) if comment else 0
    )
    if existing_round >= marker["round"]:
        raise SummaryError("A current or newer review summary already exists.")
    if comment_id is None:
        arguments = ["api", "-X", "POST", f"repos/{repo}/issues/{number}/comments"]
        action = "created"
    else:
        arguments = ["api", "-X", "PATCH", f"repos/{repo}/issues/comments/{comment_id}"]
        action = "updated"
    live = run_gh(
        ["api", f"repos/{repo}/pulls/{number}", "--jq", "[.head.sha, .base.sha] | @tsv"]
    )
    if live.returncode != 0:
        raise SummaryError(
            f"Could not verify the live pull request: {live.stderr.strip()}"
        )
    expected_live = f"{marker['sha']}\t{marker['base']}"
    if live.stdout.strip() != expected_live:
        raise SummaryError("The pull request changed before summary publication.")
    result = run_gh([*arguments, "-f", f"body={body}"])
    if result.returncode != 0:
        verb = "create" if action == "created" else "update"
        raise SummaryError(
            f"Could not {verb} the review summary: {result.stderr.strip()}"
        )
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SummaryError("GitHub returned an invalid publication record.") from error
    if comment_id is not None and response.get("id") != comment_id:
        raise SummaryError("GitHub updated a different summary comment.")
    if response.get("body") != body:
        raise SummaryError("GitHub did not store the complete review summary.")
    return action


def write_output(body: str, output: Path | None) -> None:
    if output is None:
        print(body)
        return
    output.write_text(body + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    round_parser = commands.add_parser("next-round")
    round_parser.add_argument("--previous-verdict", type=Path)
    round_parser.add_argument("--legacy-rounds", type=int, required=True)
    round_parser.add_argument("--marker-round", type=int, required=True)

    marker_parser = commands.add_parser("marker-round")
    marker_parser.add_argument("--history", type=Path, required=True)
    marker_parser.add_argument("--base")

    validate_parser = commands.add_parser("validate-verdict")
    validate_parser.add_argument("--verdict", type=Path, required=True)
    validate_parser.add_argument("--sha", required=True)
    validate_parser.add_argument("--round", type=int, required=True)
    validate_parser.add_argument("--lane-comments", type=Path, action="append")
    validate_parser.add_argument("--runeseer-comments", type=Path, action="append")
    validate_parser.add_argument("--previous-verdict", type=Path)

    format_parser = commands.add_parser("format")
    format_parser.add_argument("--verdict", type=Path, required=True)
    format_parser.add_argument("--summary", type=Path, required=True)
    format_parser.add_argument("--sha", required=True)
    format_parser.add_argument("--round", type=int, required=True)
    format_parser.add_argument("--run-url", required=True)
    format_parser.add_argument("--lane-comments", type=Path, action="append")
    format_parser.add_argument("--runeseer-comments", type=Path, action="append")
    format_parser.add_argument("--previous-verdict", type=Path)
    format_parser.add_argument("--output", type=Path)

    publish_parser = commands.add_parser("publish")
    publish_parser.add_argument("--body", type=Path, required=True)
    publish_parser.add_argument("--repo", required=True)
    publish_parser.add_argument("--pr", type=int, required=True)
    publish_parser.add_argument("--author", default="runeseer[bot]")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        if arguments.command == "next-round":
            print(
                next_round(
                    arguments.previous_verdict,
                    arguments.legacy_rounds,
                    arguments.marker_round,
                )
            )
            return 0
        if arguments.command == "marker-round":
            print(
                marker_round(
                    arguments.history.read_text(encoding="utf-8"), arguments.base
                )
            )
            return 0
        if arguments.command == "validate-verdict":
            validate_verdict(
                load_json(arguments.verdict),
                arguments.sha,
                arguments.round,
                load_lane_comments(arguments.lane_comments),
                load_lane_comments(arguments.runeseer_comments),
                load_previous_findings(arguments.previous_verdict),
            )
            return 0
        if arguments.command == "format":
            body = format_review(
                arguments.verdict,
                arguments.summary,
                arguments.sha,
                arguments.round,
                arguments.run_url,
                arguments.lane_comments,
                arguments.runeseer_comments,
                arguments.previous_verdict,
            )
            write_output(body, arguments.output)
            return 0
        if arguments.command != "publish":
            raise SummaryError(f"The command is not implemented: {arguments.command}")

        body = arguments.body.read_text(encoding="utf-8").rstrip("\n")
        try:
            action = publish_summary(
                body, arguments.repo, arguments.pr, arguments.author
            )
        except SummaryError:
            print(
                "The review summary publication failed. Intended summary:",
                file=sys.stderr,
            )
            print(body, file=sys.stderr)
            raise
        print(f"The workflow {action} the current review summary.")
        return 0
    except (OSError, SummaryError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
