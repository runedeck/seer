#!/usr/bin/env python3
"""Manage deterministic Runeseer review output."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SUMMARY_MARKER = "<!-- runeseer-review -->"
VERDICT_MARKER = (
    "<!-- runeseer-verdict sha={sha} base={base} round={round} "
    "verdict={verdict} restart={restart}{generation} -->"
)
VERDICT_MARKER_RE = re.compile(
    r"<!-- runeseer-verdict sha=(?P<sha>[0-9a-f]{40}) "
    r"base=(?P<base>[0-9a-f]{40}) round=(?P<round>[0-9]+) "
    r"verdict=(?P<verdict>clean|findings)"
    r"(?: restart=(?P<restart>none|cursor|macroscope))?"
    r"(?: generation=(?P<generation>[1-9][0-9]*))? -->"
)
REVIEW_FOOTER_RE = re.compile(
    r"^(?:No open findings|1 open|[1-9][0-9]* open) · "
    r"Reviewed `(?P<sha>[0-9a-f]{8})` · "
    r"\[review run\]\([^\r\n)]*/actions/runs/(?P<run>[1-9][0-9]*)\)"
    r"(?: · [^\r\n]+)?$"
)
VERDICTS = {"clean", "findings"}
RESTARTS = {"none", "macroscope"}
# The lane table on the protected default branch is the one source of
# lanes. These maps hold the built-in table until configure_lanes replaces
# them from the fetched table, so a lane added as one table row reaches
# the formatter without a code change.
LANE_LOGINS = {
    "cursor[bot]": "cursor",
    "macroscopeapp[bot]": "macroscope",
    "coderabbitai[bot]": "coderabbit",
    "chatgpt-codex-connector[bot]": "codex",
}
LANE_NAMES = {
    "runeseer": "Runeseer",
    "cursor": "Cursor Bugbot",
    "macroscope": "Macroscope",
    "coderabbit": "CodeRabbit",
    "codex": "Codex",
}
REVIEWER_LANE = "runeseer"
# The controller's ledger. The lane table on the protected default branch
# names the expected lanes; every one of them records one of these states
# on every head. Dispositions come from the verdict, never from the
# platform's resolved flag.
LEDGER_SCHEMA = 1
LANE_STATUSES = frozenset(
    {
        "completed",
        "completed-no-findings",
        "skipped",
        "ineligible",
        "failed",
        "rate-limited",
        "pending",
    }
)
LANE_KINDS = frozenset({"free", "paid"})
DISPOSITIONS = frozenset({"fixed", "rejected", "owner"})
PAID_ROUND_LIMIT = 3
LANE_VOLUME_LIMIT = 40
# The judged scope: the specifications, the delta specs of a change, the
# runes, and everything under .github. A file removed or renamed is never
# prose, whatever its name.
PROSE_SCOPE_RE = re.compile(r"^(docs/specs/|docs/changes/[^/]+/specs/|runes/|\.github/)")
NEVER_PROSE_STATUSES = frozenset({"D", "removed", "renamed", "renamed-from", "copied"})
ROUND_START_MARKER = "<!-- runeseer-round-start sha={sha} base={base} round={round} -->"
ROUND_START_RE = re.compile(
    r"<!-- runeseer-round-start sha=(?P<sha>[0-9a-f]{40}) base=(?P<base>[0-9a-f]{40})"
    r" round=(?P<round>[1-9][0-9]*) -->"
)
INSTRUCTION_PATTERN = (
    r"(^|/)(CLAUDE|AGENTS)(\.local)?\.md$|^\.claude(/|$)|^\.cursor(/|$)"
    r"|(^|/)copilot-instructions\.md$|^\.mcp\.json$|^\.rune$"
)
WORK_ITEM_RE = re.compile(r"docs/changes/([A-Za-z0-9][A-Za-z0-9._-]*)")
RATE_LIMIT_RE = re.compile(r"rate.?limit|usage exhausted|quota", re.IGNORECASE)
FAILED_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "action_required", "stale", "cancelled", "startup_failure"}
)
PROHIBITED_LINES = (
    "lane judgments",
    "digest",
    "carried",
    "confirmed",
    "disputed",
    "already addressed",
)
MODEL_TEXT_BYTE_LIMIT = 4096
SUMMARY_BYTE_LIMIT = MODEL_TEXT_BYTE_LIMIT
MAX_OPEN_FINDINGS = 50
MAX_LANE_JUDGMENTS = 200
MAX_RDJSONL_RECORD_BYTES = 16384
MAX_GITHUB_COMMENT_BYTES = 60000
MAX_CONTEXT_RECORDS = 40
MAX_CONTEXT_BYTES = 65536
VERDICT_PREFIXES = {
    "clean": "**Looks good.**",
    "findings": "**Request changes.**",
}
FAILURE_NOTICES = {
    "provider": (
        "### Runeseer needs attention — review action failed",
        "The review action failed, so Runeseer could not complete the review.",
        "Inspect the failed action. Restore review capacity only when the log reports an exhausted limit.",
    ),
    "missing": (
        "### Runeseer needs attention — required output missing",
        "The review action finished, but it did not create a required review file.",
        "Request a new review round. This result does not show a provider outage.",
    ),
    "invalid": (
        "### Runeseer needs attention — invalid review output",
        "Runeseer received a verdict, but deterministic validation rejected it.",
        "Inspect the validation error. Then request a new review round.",
    ),
    "publication": (
        "### Runeseer needs attention — summary publication failed",
        "The verdict passed validation, but Runeseer did not publish its summary.",
        "Inspect the publication error. Do not spend another review round until publication works.",
    ),
    "workflow": (
        "### Runeseer needs attention — workflow stopped before review",
        "The workflow stopped before the Claude review action started.",
        "Inspect the failed step. Then request a new review round.",
    ),
    "workflow_after_review": (
        "### Runeseer needs attention — workflow stopped after review",
        "The review action produced a verdict, but a later workflow step prevented publication.",
        "Inspect the failed step. Then request a new review round.",
    ),
    "workflow_after_publication": (
        "### Runeseer needs attention — workflow stopped after publication",
        "Runeseer published the verdict, but a later workflow step failed.",
        "Inspect the failed step. Then request a new review round.",
    ),
}
FAILURE_RETRY_GUIDANCE = (
    "Rerun this workflow only when the pull request head is unchanged. "
    "Otherwise, clear `issue:rune` and request a new round."
)
FAILURE_MARKER_RE = re.compile(
    r"<!-- runeseer-failure-notice "
    r"head=(?P<head>[0-9a-f]{40}) "
    r"base=(?P<base>[0-9a-f]{40}) "
    r"run=(?P<run>[1-9][0-9]*) "
    r"stage=(?P<stage>[a-z_]+) -->"
)
LEGACY_FAILURE_MARKER_RE = re.compile(
    r"<!-- runeseer-provider-failure:[0-9a-f]{40} -->"
)
OWNER_ESCALATION_MARKER_RE = re.compile(
    r"<!-- runeseer-owner-escalation "
    r"head=(?P<head>[0-9a-f]{40}) "
    r"base=(?P<base>[0-9a-f]{40}) "
    r"run=(?P<run>[1-9][0-9]*) -->"
)
LEGACY_OWNER_ESCALATION_MARKER_RE = re.compile(
    r"<!-- runeseer-escalation:[A-Za-z0-9+/]+={0,2} -->"
)
LEGACY_OWNER_ESCALATION_PREFIX = (
    "@runedeck, these findings need an owner decision before merge:"
)
OWNER_ACTIONS = (
    "- Fix all findings. Then apply `review:runeseer` to review the new head.",
    "- Apply `ignore:runeseer` to accept the complete current verdict.",
)
REVIEW_SIZE_FALLBACK = (
    "Inline findings and the verdict artifact contain the complete details."
)
OWNER_SIZE_FALLBACK = "GitHub cannot store the full finding list in one comment."
OWNER_DETAILS_FALLBACK = (
    "The Runeseer review and verdict artifact contain the complete details."
)
TRANSIENT_NOTICE_MARKERS = {
    "failure": (FAILURE_MARKER_RE, LEGACY_FAILURE_MARKER_RE, "failure notice"),
    "owner": (
        OWNER_ESCALATION_MARKER_RE,
        LEGACY_OWNER_ESCALATION_MARKER_RE,
        "owner escalation",
    ),
}
STEP_OUTCOMES = {"success", "failure", "cancelled", "skipped"}
JOB_STATUSES = {"success", "failure", "cancelled"}
REVIEWDOG_BODY_PREFIX = (
    "<sub>reported by [reviewdog](https://github.com/reviewdog/reviewdog) "
    ":dog:</sub><br>"
)
REVIEWDOG_TOOL_HEADER = "**[runeseer]**"
REVIEWDOG_SEVERITY_ICONS = {
    "critical": "🚫",
    "high": "🚫",
    "medium": "⚠️",
}
PERSONAL_OWNER_RE = re.compile(r"^@[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


class SummaryError(RuntimeError):
    """Report invalid review state or a publication failure."""


def utf8_size(value: str) -> int:
    """Return the UTF-8 byte count for text."""
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise SummaryError("Text must use valid UTF-8 encoding.") from error


def read_utf8(path: Path) -> str:
    """Read valid UTF-8 text and normalize file and encoding failures."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SummaryError(
            f"Could not read valid UTF-8 from {path}: {error}"
        ) from error


def validate_model_text_size(value: str, field: str) -> str:
    """Require one model-authored text field to fit its byte boundary."""
    if utf8_size(value) > MODEL_TEXT_BYTE_LIMIT:
        raise SummaryError(
            f"{field} must not exceed {MODEL_TEXT_BYTE_LIMIT} UTF-8 bytes."
        )
    return value


def validate_model_text_values(value: Any, field: str) -> None:
    """Validate every model-authored string in a nested value."""
    if isinstance(value, str):
        validate_model_text_size(value, field)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_model_text_values(item, f"{field}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            validate_model_text_values(item, f"{field}.{key}")


def validate_comment_body(body: str, field: str) -> str:
    """Require one complete GitHub comment body to fit its byte boundary."""
    if utf8_size(body) > MAX_GITHUB_COMMENT_BYTES:
        raise SummaryError(
            f"{field} must not exceed {MAX_GITHUB_COMMENT_BYTES} UTF-8 bytes."
        )
    return body


def repository_owner(codeowners: str) -> str:
    """Return the first personal account in the effective catch-all rule."""
    selected: list[str] | None = None
    for raw_line in codeowners.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields: list[str] = []
        for field in line.split():
            if field.startswith("#"):
                break
            fields.append(field)
        if not fields or fields[0] != "*":
            continue
        selected = [field for field in fields[1:] if PERSONAL_OWNER_RE.fullmatch(field)]
    if selected is None:
        raise SummaryError(
            "The trusted CODEOWNERS file has no repository-wide owner rule."
        )
    if not selected:
        raise SummaryError(
            "The trusted CODEOWNERS rule has no personal GitHub account."
        )
    return selected[0]


def format_owner_escalation(
    owner: str, findings: Any, head: str, base: str, run_id: int
) -> str:
    """Render one complete-verdict decision request for a human owner."""
    if PERSONAL_OWNER_RE.fullmatch(owner) is None:
        raise SummaryError("The escalation owner must be a personal GitHub handle.")
    if not isinstance(findings, list) or not findings:
        raise SummaryError("The owner escalation needs at least one finding.")
    if len(findings) > MAX_OPEN_FINDINGS:
        raise SummaryError(
            f"The owner escalation must not exceed {MAX_OPEN_FINDINGS} findings."
        )
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise SummaryError("The owner escalation needs a full commit SHA.")
    if re.fullmatch(r"[0-9a-f]{40}", base) is None:
        raise SummaryError("The owner escalation needs a full base SHA.")
    if type(run_id) is not int or run_id < 1:
        raise SummaryError("The owner escalation needs a positive run ID.")

    lines = [f"{owner}, the following findings block this head:", ""]
    for finding in findings:
        if not isinstance(finding, dict):
            raise SummaryError("Each owner escalation finding must be an object.")
        path = validate_plain_text(
            finding.get("path"), "Each owner escalation finding path"
        )
        line = finding.get("line")
        if type(line) is not int or line < 0:
            raise SummaryError(
                "Each owner escalation finding needs a nonnegative line number."
            )
        summary = validate_plain_text(
            finding.get("summary"), "Each owner escalation finding summary"
        )
        lines.append(f"- `{path}:{line}`: {summary}")
        escalation_key = finding.get("escalation_key")
        if escalation_key is not None:
            if (
                not isinstance(escalation_key, str)
                or re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", escalation_key) is None
            ):
                raise SummaryError("Each escalation key must be valid Base64 text.")
            validate_model_text_size(escalation_key, "Each escalation key")
            lines.append(f"<!-- runeseer-escalation:{escalation_key} -->")

    marker = f"<!-- runeseer-owner-escalation head={head} base={base} run={run_id} -->"
    lines.extend(
        (
            "",
            "Choose one action:",
            "",
            *OWNER_ACTIONS,
            "",
            marker,
        )
    )
    body = "\n".join(lines)
    if utf8_size(body) <= MAX_GITHUB_COMMENT_BYTES:
        return body

    count = len(findings)
    noun = "finding" if count == 1 else "findings"
    verb = "blocks" if count == 1 else "block"
    compact = "\n".join(
        (
            f"{owner}, {count} {noun} {verb} this head:",
            "",
            OWNER_SIZE_FALLBACK,
            OWNER_DETAILS_FALLBACK,
            "",
            "Choose one action:",
            "",
            *OWNER_ACTIONS,
            "",
            marker,
        )
    )
    return validate_comment_body(compact, "The owner escalation")


def parse_reviewdog_comment(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise SummaryError("Each Reviewdog comment needs a valid body.")
    header, separator, message = value.partition(REVIEWDOG_BODY_PREFIX)
    match = re.match(r"^\*\*(Critical|High|Medium)\*\*", message)
    if not separator or match is None:
        raise SummaryError("Each Runeseer comment needs the Reviewdog v0.21.0 wrapper.")
    severity = match.group(1).lower()
    expected_header = f"{REVIEWDOG_SEVERITY_ICONS[severity]} {REVIEWDOG_TOOL_HEADER} "
    if header != expected_header:
        raise SummaryError(
            "Each Runeseer comment needs the exact Reviewdog tool header."
        )
    return severity


def load_json(path: Path) -> Any:
    try:
        return json.loads(read_utf8(path))
    except json.JSONDecodeError as error:
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


def collect_lane_evidence(
    inline: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    head: str,
    previous_findings: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Bind bot evidence to its source and the head under adjudication."""
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise SummaryError("Lane collection needs a full head SHA.")
    for records in (inline, issues, reviews):
        if not isinstance(records, list) or not all(
            isinstance(record, dict) for record in records
        ):
            raise SummaryError("Each lane response must contain an array of objects.")

    def trusted(record: dict[str, Any]) -> bool:
        user = record.get("user")
        return (
            isinstance(user, dict)
            and user.get("type") == "Bot"
            and user.get("login") in LANE_LOGINS
            and type(record.get("id")) is int
            and record["id"] > 0
        )

    result: dict[str, list[dict[str, Any]]] = {
        "inline-comments": [],
        "issue-comments": [],
        "review-bodies": [],
    }
    for record in inline:
        if not trusted(record):
            continue
        commit = record.get("commit_id")
        if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            continue
        item = copy.deepcopy(record)
        item["lane"] = LANE_LOGINS[item["user"]["login"]]
        item["adjudication_head"] = head
        item["head_binding"] = "current" if commit == head else "historical"
        # Historical findings still need a code-level judgment. A new head
        # must not erase an unresolved finding from the previous head.
        result["inline-comments"].append(item)
    for record in issues:
        if not trusted(record):
            continue
        # CodeRabbit walkthrough and skipped-review comments have no GitHub
        # commit binding. Its submitted reviews and inline findings do.
        if record["user"]["login"] == "coderabbitai[bot]":
            continue
        item = copy.deepcopy(record)
        item["lane"] = LANE_LOGINS[item["user"]["login"]]
        item["adjudication_head"] = head
        item["head_binding"] = "unbound"
        result["issue-comments"].append(item)
    carried_inline = {
        (finding.get("lane"), finding.get("comment_id"))
        for finding in previous_findings or []
        if finding.get("source_kind", "comment") == "comment"
        and finding.get("lane") in LANE_LOGINS.values()
    }
    collected_inline = {
        (record["lane"], record["id"]) for record in result["inline-comments"]
    }
    if not carried_inline <= collected_inline:
        raise SummaryError("A carried inline finding has no bound source comment.")
    carried_reviews = {
        (finding.get("lane"), finding.get("comment_id"))
        for finding in previous_findings or []
        if finding.get("source_kind") == "review"
    }
    for record in reviews:
        if not trusted(record):
            continue
        user = record["user"]
        carried = (
            LANE_LOGINS.get(user.get("login")),
            record.get("id"),
        ) in carried_reviews
        commit = record.get("commit_id")
        if (
            not isinstance(commit, str)
            or re.fullmatch(r"[0-9a-f]{40}", commit) is None
            or (commit != head and not carried)
            or not (
                record.get("state") in {"COMMENTED", "CHANGES_REQUESTED", "APPROVED"}
                or (carried and record.get("state") == "DISMISSED")
            )
            or not record.get("submitted_at")
            or not isinstance(record.get("body"), str)
            or not record["body"].strip()
        ):
            continue
        item = copy.deepcopy(record)
        item["source_kind"] = "review"
        item["lane"] = LANE_LOGINS[item["user"]["login"]]
        item["adjudication_head"] = head
        item["head_binding"] = "current" if commit == head else "historical"
        item["carried"] = carried
        result["review-bodies"].append(item)
    collected_reviews = {
        (record["lane"], record["id"]) for record in result["review-bodies"]
    }
    if not carried_reviews <= collected_reviews:
        raise SummaryError(
            "A carried review-body finding has no submitted source review."
        )
    return result


def collect_rebuttal_context(
    inline: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    head: str,
    lane_comments: list[dict[str, Any]],
    previous_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Collect typed provider facts and reply references, never reply prose."""
    roots = {
        item["id"] for item in lane_comments
        if item.get("in_reply_to_id") is None
    } | {
        item["comment_id"] for item in previous_findings
        if item.get("source_kind", "comment") == "comment"
        and type(item.get("comment_id")) is int
        and item["comment_id"] > 0
    }
    entries = []
    notice_ids = {}
    notice_url = re.compile(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/"
        r"[1-9][0-9]*#issuecomment-([1-9][0-9]*)"
    )
    for record in issues:
        user = record.get("user")
        body = record.get("body")
        if (
            not isinstance(user, dict)
            or not isinstance(user.get("login"), str)
            or type(record.get("id")) is not int
            or record["id"] <= 0
            or not isinstance(body, str)
            or utf8_size(body) > MAX_GITHUB_COMMENT_BYTES
        ):
            continue
        entry = {
            "id": record["id"],
            "author": user["login"][:100],
            "url": str(record.get("html_url", ""))[:1024],
            "updated_at": str(record.get("updated_at", ""))[:40],
        }
        if user.get("login") == "coderabbitai[bot]" and user.get("type") == "Bot":
            # Keep configuration metadata, not the walkthrough or echoed PR text.
            url_match = notice_url.fullmatch(entry["url"])
            if url_match is None or int(url_match[1]) != record["id"]:
                continue
            if "<!-- This is an auto-generated comment: skip review by coderabbit.ai -->" not in body:
                continue
            source = re.search(
                r"(?m)^>\s*\*\*Configuration used\*\*:\s*"
                r"(Organization UI|Repository UI|Central YAML|Repository YAML)\s*$",
                body,
            )
            labels = re.search(
                r"<summary>[^\n]*Required labels[^\n]*</summary>(.*?)</details>",
                body, re.DOTALL,
            )
            if source is None or labels is None:
                continue
            required = re.findall(r"(?m)^>\s*\* ([^\r\n]{1,100})$", labels[1])
            if (
                not required or len(required) > 20
                or any(re.fullmatch(r"[A-Za-z0-9_:.-]{1,100}", label) is None for label in required)
            ):
                continue
            entry.update(
                kind="coderabbit_configuration_notice",
                configuration_source=source[1],
                required_labels=required,
            )
        else:
            continue
        entries.append(entry)
        notice_ids[entry["url"]] = entry["id"]
    entries.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)
    result: dict[str, Any] = {
        "schema_version": 1, "head": head, "trust": "untrusted",
        "entries": [], "omitted_records": 0,
    }
    for entry in entries:
        result["entries"].append(entry)
        if (
            len(result["entries"]) > MAX_CONTEXT_RECORDS
            or utf8_size(json.dumps(result, ensure_ascii=False)) > MAX_CONTEXT_BYTES - 128
        ):
            result["entries"].pop()
            result["omitted_records"] += 1
    # Only link to typed notices that remain in this bounded evidence snapshot.
    # A reply cannot introduce a URL to fetch, a new fact, or model instructions.
    retained_ids = {entry["id"] for entry in result["entries"]}
    for record in reversed(inline):
        user = record.get("user")
        body = record.get("body")
        if (
            not isinstance(user, dict) or user.get("type") != "User"
            or type(record.get("id")) is not int or record["id"] <= 0
            or type(record.get("in_reply_to_id")) is not int
            or record["in_reply_to_id"] not in roots
            or not isinstance(body, str) or utf8_size(body) > MAX_GITHUB_COMMENT_BYTES
        ):
            continue
        references = sorted({
            notice_ids[match[0]] for match in notice_url.finditer(body)
            if match[0] in notice_ids and notice_ids[match[0]] in retained_ids
        })
        if not references:
            continue
        result["entries"].append({
            "id": record["id"], "kind": "finding_reply",
            "root_comment_id": record["in_reply_to_id"],
            "configuration_notice_ids": references,
        })
        if (
            len(result["entries"]) > MAX_CONTEXT_RECORDS
            or utf8_size(json.dumps(result, ensure_ascii=False)) > MAX_CONTEXT_BYTES - 128
        ):
            result["entries"].pop()
            result["omitted_records"] += 1
    return result


def lane_source_key(item: dict[str, Any], *, source: bool = False) -> tuple[str, int]:
    """Keep review IDs separate from comment IDs."""
    return item.get("source_kind", "comment"), item["id" if source else "comment_id"]


def finding_identity(item: dict[str, Any]) -> tuple[Any, ...]:
    """Distinguish defects in one review body and retain strict inline identity."""
    source_kind = item.get("source_kind", "comment")
    return (
        item.get("lane"),
        item.get("path"),
        item.get("line"),
        item.get("comment_id"),
        source_kind,
        item.get("summary") if source_kind == "review" else None,
    )


def matching_lane_thread(
    judgment: dict[str, Any], threads: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Match one inline judgment to the same provider and root comment."""
    validate_lane_judgment(judgment)
    if judgment.get("source_kind", "comment") != "comment":
        return None
    logins = {
        identity
        for login, lane in LANE_LOGINS.items()
        if lane == judgment["lane"]
        for identity in (login, login.removesuffix("[bot]"))
    }
    matches = []
    for thread in threads:
        comments = thread.get("comments", {}).get("nodes", [])
        root = comments[0] if comments else {}
        author = root.get("author") or {}
        if (
            author.get("__typename") == "Bot"
            and author.get("login") in logins
            and root.get("databaseId") == judgment["comment_id"]
            and thread.get("path") == judgment["path"]
            and (thread.get("line") or thread.get("originalLine") or 0)
            == judgment["line"]
        ):
            matches.append(thread)
    if len(matches) > 1:
        raise SummaryError("A lane judgment matches more than one review thread.")
    return matches[0] if matches else None


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


def load_ledger(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return validate_ledger(load_json(path))


def read_stored_ledger(
    verdict: Any, expected_sha: str, expected_round: int
) -> dict[str, Any]:
    """Normalize an obsolete restart only when reading a stored ledger."""
    stored = copy.deepcopy(verdict)
    if isinstance(stored, dict) and stored.get("restart") == "cursor":
        stored["historical_restart"] = "cursor"
        stored["restart"] = "none"
    return validate_verdict(stored, expected_sha, expected_round)


def load_runeseer_records(path: Path | None) -> list[dict[str, Any]] | None:
    if path is None:
        return None
    lines = read_utf8(path).splitlines()
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        if utf8_size(line) > MAX_RDJSONL_RECORD_BYTES:
            raise SummaryError(
                f"Runeseer finding line {line_number} must not exceed "
                f"{MAX_RDJSONL_RECORD_BYTES} UTF-8 bytes."
            )
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise SummaryError(
                f"Runeseer finding line {line_number} must contain valid JSON."
            ) from error
        if not isinstance(record, dict):
            raise SummaryError(
                f"Runeseer finding line {line_number} must contain an object."
            )
        validate_model_text_values(record, f"Runeseer finding line {line_number}")
        records.append(record)
    return records


def runeseer_record_key(record: dict[str, Any]) -> tuple[str, int, str]:
    validate_model_text_values(record, "Each Runeseer finding record")
    message = record.get("message")
    if not isinstance(message, str) or not message:
        raise SummaryError("Each Runeseer finding record needs a message.")
    match = re.match(r"^\*\*(Critical|High|Medium)\*\* — ", message)
    if match is None:
        raise SummaryError("Each Runeseer finding message needs a severity prefix.")
    severity = match.group(1).lower()
    expected_level = "WARNING" if severity == "medium" else "ERROR"
    if record.get("severity") != expected_level:
        raise SummaryError("Each Runeseer finding severity must match its message.")
    location = record.get("location")
    location = location if isinstance(location, dict) else {}
    path = location.get("path")
    range_value = location.get("range")
    range_value = range_value if isinstance(range_value, dict) else {}
    start = range_value.get("start")
    start = start if isinstance(start, dict) else {}
    line = start.get("line")
    validate_plain_text(path, "Each Runeseer finding path")
    if type(line) is not int or line < 0:
        raise SummaryError("Each Runeseer finding needs a nonnegative line number.")
    return path, line, severity


def validate_runeseer_records(
    findings: list[dict[str, Any]], records: list[dict[str, Any]]
) -> None:
    finding_keys = sorted(
        (finding["path"], finding["line"], finding["severity"])
        for finding in findings
        if finding["lane"] == "runeseer"
    )
    record_keys = sorted(runeseer_record_key(record) for record in records)
    if finding_keys != record_keys:
        raise SummaryError(
            "The Runeseer verdict and finding records must contain the same findings."
        )


def validate_lane_bindings(
    judgments: list[dict[str, Any]],
    lane_comments: list[dict[str, Any]],
    nonfinding_issue_ids: list[int],
    expected_sha: str | None = None,
) -> None:
    sources: dict[tuple[str, int], tuple[str, dict[str, Any]]] = {}
    required_inline_ids: set[int] = set()
    review_ids: set[int] = set()
    issue_ids: set[int] = set()
    for comment in lane_comments:
        comment_id = comment.get("id")
        user = comment.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        lane = LANE_LOGINS.get(login)
        if type(comment_id) is not int or lane is None:
            continue
        if (
            expected_sha is not None
            and comment.get("adjudication_head", expected_sha) != expected_sha
        ):
            raise SummaryError(
                "Lane evidence belongs to a different adjudication head."
            )
        key = lane_source_key(comment, source=True)
        if key in sources:
            raise SummaryError("A trusted lane comment ID is ambiguous.")
        sources[key] = (lane, comment)
        if key[0] == "review":
            current = (
                comment.get("head_binding") == "current"
                and comment.get("commit_id") == expected_sha
            )
            carried = (
                comment.get("head_binding") == "historical"
                and comment.get("carried") is True
                and isinstance(comment.get("commit_id"), str)
                and re.fullmatch(r"[0-9a-f]{40}", comment["commit_id"]) is not None
            )
            if not current and not carried:
                raise SummaryError("Each review body must bind to the current head.")
            review_ids.add(comment_id)
        elif isinstance(comment.get("path"), str):
            if comment.get("in_reply_to_id") is None:
                required_inline_ids.add(comment_id)
        else:
            issue_ids.add(comment_id)

    judged_ids: set[int] = set()
    judged_reviews: set[int] = set()
    review_anchors: set[tuple[Any, ...]] = set()
    for judgment in judgments:
        # Ledger rechecks judge Runeseer's own earlier findings. Those
        # comments live outside the external lane files, so the external
        # binding rules below cannot apply to them.
        if judgment.get("lane") == "runeseer":
            continue
        comment_id = judgment["comment_id"]
        key = lane_source_key(judgment)
        if key[0] == "comment" and comment_id in judged_ids:
            raise SummaryError("Each lane comment can have only one judgment.")
        source = sources.get(key)
        if source is None:
            raise SummaryError(
                "Each lane judgment comment ID must identify a fetched lane comment."
            )
        source_lane, comment = source
        if judgment["lane"] != source_lane:
            raise SummaryError("Each lane judgment must preserve its source lane.")
        if key[0] == "review":
            anchor = finding_identity(judgment)
            if anchor in review_anchors:
                raise SummaryError("Each review-body finding needs a unique anchor.")
            review_anchors.add(anchor)
            judged_reviews.add(comment_id)
            continue
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
    if review_ids != judged_reviews:
        raise SummaryError("Every fetched review body needs a lane judgment.")

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
    allow_unposted: bool = False,
) -> None:
    """Bind each posted Runeseer comment to one machine finding.

    Reviewdog can filter a record outside the diff context. The summary keeps that finding
    visible and blocking when the validated findings file proves that the model emitted it.
    """
    sources: dict[int, dict[str, Any]] = {}
    for comment in comments:
        comment_id = comment.get("id")
        if type(comment_id) is int and comment.get("in_reply_to_id") is None:
            sources[comment_id] = comment

    def comment_key(comment: dict[str, Any]) -> tuple[Any, Any, Any]:
        try:
            severity = parse_reviewdog_comment(comment.get("body"))
        except SummaryError:
            severity = None
        return (
            comment.get("path"),
            comment.get("line") or comment.get("original_line"),
            severity,
        )

    own_findings = [
        finding for finding in findings if finding.get("lane") == "runeseer"
    ]
    previous_own = [
        finding for finding in previous_findings if finding.get("lane") == "runeseer"
    ]
    previous_by_id = {
        finding["comment_id"]: finding
        for finding in previous_own
        if type(finding.get("comment_id")) is int
    }

    unclaimed = dict(sources)
    for finding in own_findings:
        if finding.get("comment_id") is not None:
            continue
        anchor = (finding.get("path"), finding.get("line"), finding.get("severity"))
        matches = [
            comment_id
            for comment_id, comment in unclaimed.items()
            if comment_key(comment) == anchor
        ]
        if not matches:
            if allow_unposted:
                continue
            raise SummaryError(
                "Each new Runeseer finding needs a posted inline comment at its anchor."
            )
        if len(matches) > 1:
            raise SummaryError(
                "Each new Runeseer finding needs exactly one posted inline comment."
            )
        finding["comment_id"] = matches[0]
        del unclaimed[matches[0]]

    # A carried finding can gain a fresh comment once: legacy comments carry no dedup marker,
    # so the first post-migration round re-posts still-open findings under markers. The fresh
    # comment becomes the finding's evidence; the legacy comment stays for its thread.
    for finding in own_findings:
        comment_id = finding.get("comment_id")
        if comment_id is None or comment_id in sources:
            continue
        anchor = (finding.get("path"), finding.get("line"), finding.get("severity"))
        matches = [
            candidate
            for candidate, comment in unclaimed.items()
            if comment_key(comment) == anchor
        ]
        if len(matches) == 1:
            finding["comment_id"] = matches[0]
            del unclaimed[matches[0]]

    finding_ids = {
        finding["comment_id"]
        for finding in own_findings
        if finding.get("comment_id") is not None
    }
    if set(sources) - finding_ids:
        raise SummaryError("Every new Runeseer inline comment needs an open finding.")
    findings_by_id = {
        finding["comment_id"]: finding
        for finding in own_findings
        if finding.get("comment_id") is not None
    }
    for comment_id in set(sources) & finding_ids:
        comment = sources[comment_id]
        finding = findings_by_id[comment_id]
        if comment_key(comment) != (
            finding.get("path"),
            finding.get("line"),
            finding.get("severity"),
        ):
            raise SummaryError(
                "Each Runeseer finding must preserve its inline anchor and severity."
            )
    for finding in own_findings:
        comment_id = finding.get("comment_id")
        if comment_id is None or comment_id in sources:
            continue
        previous = previous_by_id.get(comment_id)
        if previous is None:
            raise SummaryError(
                "Each Runeseer finding ID needs new or carried evidence."
            )
        if (
            finding.get("path"),
            finding.get("line"),
            finding.get("severity"),
        ) != (
            previous.get("path"),
            previous.get("line"),
            previous.get("severity"),
        ):
            raise SummaryError(
                "Each carried Runeseer finding must preserve its path, line, and severity."
            )


def validate_plain_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SummaryError(f"{field} must be a nonempty string.")
    if "\n" in value or "\r" in value or "<!--" in value or "-->" in value:
        raise SummaryError(f"{field} must be one plain-text line.")
    return validate_model_text_size(value, field)


def validate_lane_judgment(item: Any) -> dict[str, Any]:
    """Validate one model-authored lane judgment before trusted binding."""
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
    if item.get("source_kind", "comment") not in {"comment", "review"}:
        raise SummaryError("Each lane judgment needs a known source kind.")
    if item.get("source_kind") == "review" and item.get("lane") == "runeseer":
        raise SummaryError(
            "A Runeseer ledger judgment must identify an inline comment."
        )
    comment_id = item.get("comment_id")
    if type(comment_id) is not int or comment_id < 1:
        raise SummaryError("Each lane judgment comment ID must be a positive integer.")
    validate_plain_text(item.get("reason"), "Each lane judgment reason")
    return item


def canonicalize_external_findings(
    verdict: Any, lane_comments: list[dict[str, Any]]
) -> dict[str, Any]:
    """Derive external findings from trusted inline and review-body judgments."""
    if not isinstance(verdict, dict):
        raise SummaryError("The verdict must be a JSON object.")
    findings = verdict.get("findings")
    judgments = verdict.get("lane_judgments")
    nonfinding_issue_ids = verdict.get("nonfinding_issue_comment_ids")
    if not isinstance(findings, list):
        raise SummaryError("The findings field must be an array.")
    if not isinstance(judgments, list):
        raise SummaryError("The lane_judgments field must be an array.")
    if len(judgments) > MAX_LANE_JUDGMENTS:
        raise SummaryError(
            f"The verdict must not exceed {MAX_LANE_JUDGMENTS} lane judgments."
        )
    if not isinstance(nonfinding_issue_ids, list) or any(
        type(comment_id) is not int or comment_id < 1
        for comment_id in nonfinding_issue_ids
    ):
        raise SummaryError(
            "The reviewed issue comment IDs must be positive integer values."
        )
    if not isinstance(lane_comments, list) or not all(
        isinstance(comment, dict) for comment in lane_comments
    ):
        raise SummaryError("The lane comments must be an array of objects.")

    for judgment in judgments:
        validate_lane_judgment(judgment)
    validate_lane_bindings(
        judgments, lane_comments, nonfinding_issue_ids, verdict.get("sha")
    )

    sources: dict[tuple[str, int], tuple[str, dict[str, Any]]] = {}
    for comment in lane_comments:
        comment_id = comment.get("id")
        user = comment.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        lane = LANE_LOGINS.get(login)
        if type(comment_id) is not int or lane is None:
            continue
        key = lane_source_key(comment, source=True)
        if key in sources:
            raise SummaryError("A trusted lane comment ID is ambiguous.")
        sources[key] = (lane, comment)

    def require_finding_source(item: dict[str, Any]) -> None:
        comment_id = item.get("comment_id")
        if type(comment_id) is not int or comment_id < 1:
            raise SummaryError("Each lane finding needs its source comment ID.")
        source = sources.get(lane_source_key(item))
        if source is None:
            raise SummaryError("Each lane finding needs a trusted source comment.")
        source_lane, comment = source
        if comment.get("source_kind") == "review":
            if item.get("lane") != source_lane:
                raise SummaryError("Each review finding must preserve its source lane.")
            return
        source_path = comment.get("path")
        source_line = comment.get("line")
        if type(source_line) is not int:
            source_line = comment.get("original_line")
        if (
            comment.get("in_reply_to_id") is not None
            or not isinstance(source_path, str)
            or not source_path
            or type(source_line) is not int
            or source_line < 0
        ):
            raise SummaryError(
                "Only a trusted root inline comment can derive a lane finding."
            )
        if (item.get("lane"), item.get("path"), item.get("line")) != (
            source_lane,
            source_path,
            source_line,
        ):
            raise SummaryError(
                "Each derived lane finding must preserve its trusted source identity."
            )

    # The model does not define external findings. Preserve only Runeseer's
    # own objects for later verdict validation. Rebuild every external finding
    # from a validated trusted judgment below.
    own_findings = [
        copy.deepcopy(finding)
        for finding in findings
        if isinstance(finding, dict) and finding.get("lane") == "runeseer"
    ]
    declared_external = [
        finding
        for finding in findings
        if not (isinstance(finding, dict) and finding.get("lane") == "runeseer")
    ]

    external_findings: list[dict[str, Any]] = []
    for judgment in judgments:
        if (
            judgment["lane"] == "runeseer"
            or judgment["judgment"] != "confirmed"
            or judgment["severity"] == "low"
        ):
            continue
        require_finding_source(judgment)
        external_findings.append(
            {
                "path": judgment["path"],
                "line": judgment["line"],
                "summary": judgment["summary"],
                "lane": judgment["lane"],
                "judgment": "confirmed",
                "severity": judgment["severity"],
                "comment_id": judgment["comment_id"],
                **(
                    {"source_kind": "review"}
                    if judgment.get("source_kind") == "review"
                    else {}
                ),
            }
        )

    def complete_external_declaration(declared: Any) -> bool:
        if not isinstance(declared, dict):
            return False
        if declared.get("lane") not in LANE_LOGINS.values():
            return False
        if declared.get("judgment") != "confirmed":
            return False
        if declared.get("severity") not in {"medium", "high", "critical"}:
            return False
        if type(declared.get("line")) is not int or declared["line"] < 0:
            return False
        try:
            validate_plain_text(declared.get("path"), "Each lane finding path")
            validate_plain_text(declared.get("summary"), "Each lane finding summary")
        except SummaryError:
            return False
        declared_id = declared.get("comment_id")
        if type(declared_id) is not int or declared_id < 1:
            return False
        source = sources.get(lane_source_key(declared))
        if source is None:
            return False
        source_lane, comment = source
        if comment.get("source_kind") == "review":
            return declared["lane"] == source_lane
        source_line = comment.get("line")
        if type(source_line) is not int:
            source_line = comment.get("original_line")
        return (
            comment.get("in_reply_to_id") is None
            and isinstance(comment.get("path"), str)
            and bool(comment["path"])
            and type(source_line) is int
            and source_line >= 0
            and (declared["lane"], declared["path"], declared["line"])
            == (source_lane, comment["path"], source_line)
        )

    # Compatibility contract: discard each incomplete, malformed, or unknown
    # external entry before contradiction checks. The model does not define
    # external findings, so an invalid entry carries no authority. A complete
    # entry names one trusted root inline comment. When the judgments removed
    # that finding, the declaration and the judgments contradict each other.
    rebuilt_ids = {finding_identity(finding) for finding in external_findings}
    complete_declarations = [
        declared
        for declared in declared_external
        if complete_external_declaration(declared)
    ]
    for declared in complete_declarations:
        declared_key = finding_identity(declared)
        if declared_key not in rebuilt_ids:
            raise SummaryError(
                "The lane judgments removed a declared external finding. "
                "Runeseer rejects the contradiction instead of repairing it."
            )
    repaired = copy.deepcopy(verdict)
    repaired["findings"] = own_findings + external_findings
    if len(repaired["findings"]) > MAX_OPEN_FINDINGS:
        raise SummaryError(
            f"The verdict must not exceed {MAX_OPEN_FINDINGS} open findings."
        )
    repaired["count"] = len(repaired["findings"])
    repaired["verdict"] = "clean" if not repaired["findings"] else "findings"
    return repaired


def login_slug(login: Any) -> str:
    if not isinstance(login, str):
        return ""
    return re.sub(r"\[bot\]$", "", login)


def load_lane_table(value: Any) -> list[dict[str, Any]]:
    """Validate the lane table read from the protected default branch."""
    lanes = value.get("lanes") if isinstance(value, dict) else None
    if not isinstance(lanes, list) or not lanes:
        raise SummaryError("The lane table must contain a nonempty lanes array.")
    seen_ids: set[str] = set()
    seen_logins: set[str] = set()
    for lane in lanes:
        if not isinstance(lane, dict):
            raise SummaryError("Each lane table entry must be an object.")
        lane_id = lane.get("id")
        if not isinstance(lane_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", lane_id):
            raise SummaryError("Each lane needs a lowercase id.")
        login = lane.get("login")
        if not isinstance(login, str) or not login:
            raise SummaryError(f"The lane {lane_id} needs a login.")
        validate_plain_text(lane.get("name"), f"The lane {lane_id} name")
        if lane.get("kind") not in LANE_KINDS:
            raise SummaryError(f"The lane {lane_id} kind must be free or paid.")
        check_slug = lane.get("check_slug")
        if check_slug is not None and (
            not isinstance(check_slug, str) or not re.fullmatch(r"[a-z0-9-]+", check_slug)
        ):
            raise SummaryError(f"The lane {lane_id} check_slug must be a lowercase slug.")
        if lane_id in seen_ids or login_slug(login) in seen_logins:
            raise SummaryError(f"The lane {lane_id} repeats an id or login.")
        seen_ids.add(lane_id)
        seen_logins.add(login_slug(login))
    return lanes


def configure_lanes(lanes: list[dict[str, Any]]) -> None:
    """Replace the built-in lane maps with the fetched lane table.

    The reviewer lane never enters LANE_LOGINS: its comments are the
    verdict's own, not lane evidence for the model.
    """
    LANE_LOGINS.clear()
    LANE_NAMES.clear()
    for lane in lanes:
        LANE_NAMES[lane["id"]] = lane["name"]
        if lane["id"] != REVIEWER_LANE:
            LANE_LOGINS[lane["login"]] = lane["id"]


def lane_check_slug(lane: dict[str, Any]) -> str:
    """The app slug whose check runs report this lane, the login slug by default."""
    slug = lane.get("check_slug")
    return slug if isinstance(slug, str) and slug else login_slug(lane["login"])


def lane_for_login(lanes: list[dict[str, Any]], login: Any) -> str | None:
    slug = login_slug(login)
    if not slug:
        return None
    for lane in lanes:
        if login_slug(lane["login"]) == slug:
            return lane["id"]
    return None


def collect_threads(
    nodes: Any, lanes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Read every review thread from the API by login, never by resolved flag."""
    if not isinstance(nodes, list):
        raise SummaryError("The review threads must be an array.")
    threads: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise SummaryError("Each review thread needs a node id.")
        comments = node.get("comments")
        first_nodes = comments.get("nodes") if isinstance(comments, dict) else None
        first = first_nodes[0] if isinstance(first_nodes, list) and first_nodes else {}
        first = first if isinstance(first, dict) else {}
        author = first.get("author")
        login = author.get("login") if isinstance(author, dict) else None
        comment_id = first.get("databaseId")
        threads.append(
            {
                "id": node["id"],
                "url": first.get("url") if isinstance(first.get("url"), str) else None,
                "login": login if isinstance(login, str) else None,
                "lane": lane_for_login(lanes, login),
                "path": node.get("path"),
                "line": node.get("line") if node.get("line") is not None else node.get("originalLine"),
                "comment_id": comment_id if type(comment_id) is int and comment_id > 0 else None,
                "disposition": None,
                "reason": None,
            }
        )
    threads.sort(key=lambda thread: thread["id"])
    return threads


def check_run_status(runs: list[dict[str, Any]], has_threads: bool) -> str | None:
    """Map one lane's check runs on the head to a ledger status."""
    if not runs:
        return None
    if any(run.get("status") != "completed" for run in runs):
        return "pending"
    latest = max(runs, key=lambda run: str(run.get("completed_at") or ""))
    conclusion = latest.get("conclusion")
    output = latest.get("output") if isinstance(latest.get("output"), dict) else {}
    text = " ".join(
        str(output.get(field) or "") for field in ("title", "summary")
    )
    if RATE_LIMIT_RE.search(text):
        return "rate-limited"
    if conclusion == "skipped":
        return "skipped"
    if conclusion in FAILED_CONCLUSIONS:
        return "failed"
    if conclusion in {"success", "neutral"}:
        return "completed" if has_threads else "completed-no-findings"
    return "failed"


def lane_statuses(
    lanes: list[dict[str, Any]],
    threads: list[dict[str, Any]],
    check_runs: Any,
    labels: list[str],
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Record one status per expected lane, the controller's own view."""
    if not isinstance(check_runs, list):
        raise SummaryError("The check runs must be an array.")
    overrides = overrides or {}
    for lane_id, status in overrides.items():
        if status not in LANE_STATUSES:
            raise SummaryError(f"The lane status override for {lane_id} is invalid.")
    statuses: dict[str, str] = {}
    for lane in lanes:
        lane_id = lane["id"]
        if lane_id in overrides:
            statuses[lane_id] = overrides[lane_id]
            continue
        if f"skip:{lane_id}" in labels:
            statuses[lane_id] = "skipped"
            continue
        has_threads = any(thread["lane"] == lane_id for thread in threads)
        slug = lane_check_slug(lane)
        runs = [
            run
            for run in check_runs
            if isinstance(run, dict)
            and isinstance(run.get("app"), dict)
            and run["app"].get("slug") == slug
        ]
        status = check_run_status(runs, has_threads)
        if status is None:
            status = "completed" if has_threads else "ineligible"
        statuses[lane_id] = status
    return statuses


def work_item_from_body(body: Any, pull_number: int) -> str:
    match = WORK_ITEM_RE.search(body) if isinstance(body, str) else None
    if match is None:
        return f"pull/{pull_number}"
    return f"docs/changes/{match.group(1)}"


def body_digest(body: Any) -> str:
    text = body if isinstance(body, str) else ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_ledger(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != LEDGER_SCHEMA:
        raise SummaryError("The ledger must be a schema 1 object.")
    if not re.fullmatch(r"[0-9a-f]{40}", str(value.get("reviewed_sha", ""))):
        raise SummaryError("The ledger reviewed_sha must be a full commit SHA.")
    generation = value.get("generation")
    if type(generation) is not int or generation < 1:
        raise SummaryError("The ledger generation must be a positive integer.")
    lanes = value.get("lanes")
    if not isinstance(lanes, dict) or any(
        status not in LANE_STATUSES for status in lanes.values()
    ):
        raise SummaryError("Each ledger lane needs a known status.")
    threads = value.get("threads")
    if not isinstance(threads, list) or not all(
        isinstance(thread, dict) and isinstance(thread.get("id"), str)
        for thread in threads
    ):
        raise SummaryError("The ledger threads must be an array of thread objects.")
    for thread in threads:
        if thread.get("disposition") not in DISPOSITIONS | {None}:
            raise SummaryError(f"The ledger thread {thread['id']} has an invalid disposition.")
    return value


def build_ledger(
    *,
    pull_number: int,
    head: str,
    base: str,
    lanes: list[dict[str, Any]],
    thread_nodes: Any,
    check_runs: Any,
    labels: list[str],
    body: Any,
    previous: dict[str, Any] | None = None,
    overrides: dict[str, str] | None = None,
    paid_rounds: int = 0,
) -> dict[str, Any]:
    """Build the per-pull-request ledger for one head.

    A new thread, a lane status change, or a body edit on the same head
    increments the generation. A new head starts at generation one and
    carries no disposition forward. The reviewer lane's status is the
    controller's own record, so the same head keeps it unless an override
    names it. A work item recorded on an earlier head is bound: a body
    that names another change is a fault, not a rekey of the budget.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise SummaryError("The ledger head must be a full commit SHA.")
    if type(paid_rounds) is not int or paid_rounds < 0:
        raise SummaryError("The paid round count must be a nonnegative integer.")
    threads = collect_threads(thread_nodes, lanes)
    overrides = dict(overrides or {})
    if (
        previous is not None
        and REVIEWER_LANE not in overrides
        and previous.get("reviewed_sha") == head
        and isinstance(previous.get("lanes"), dict)
        and REVIEWER_LANE in previous["lanes"]
    ):
        overrides[REVIEWER_LANE] = previous["lanes"][REVIEWER_LANE]
    statuses = lane_statuses(lanes, threads, check_runs, labels, overrides)
    digest = body_digest(body)
    work_item = work_item_from_body(body, pull_number)
    generation = 1
    verdict: dict[str, Any] | None = None
    coverage: str | None = None
    if previous is not None:
        previous = validate_ledger(previous)
        earlier_item = previous.get("work_item")
        if (
            isinstance(earlier_item, str)
            and earlier_item.startswith("docs/changes/")
            and earlier_item != work_item
        ):
            raise SummaryError(
                f"The work item changed from {earlier_item} to {work_item}: "
                "the body must not rename the change after ready."
            )
        if previous["reviewed_sha"] == head:
            carried = {
                thread["id"]: thread
                for thread in previous["threads"]
                if thread.get("disposition") is not None
            }
            for thread in threads:
                earlier = carried.get(thread["id"])
                if earlier is not None:
                    thread["disposition"] = earlier["disposition"]
                    thread["reason"] = earlier.get("reason")
            same_threads = {thread["id"] for thread in threads} == {
                thread["id"] for thread in previous["threads"]
            }
            same_lanes = statuses == previous["lanes"]
            same_body = digest == previous.get("body_digest")
            generation = previous["generation"]
            verdict = previous.get("verdict")
            coverage = previous.get("coverage")
            if not (same_threads and same_lanes and same_body):
                generation += 1
                verdict = None
                coverage = None
    return {
        "schema": LEDGER_SCHEMA,
        "pull_request": pull_number,
        "reviewed_sha": head,
        "base": base,
        "generation": generation,
        "work_item": work_item,
        "paid_rounds": paid_rounds,
        "body_digest": digest,
        "lanes": statuses,
        "threads": threads,
        "coverage": coverage,
        "verdict": verdict,
    }


def open_threads(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        thread for thread in ledger["threads"] if thread.get("disposition") is None
    ]


def is_prose_only(files: list[str], instruction_pattern: str = INSTRUCTION_PATTERN) -> bool:
    """A diff is prose-only when every file is markdown outside the judged scope.

    Each entry is a path, or a status and a path separated by a tab as
    `git diff --name-status --no-renames` prints them. A removal or a
    rename is never prose: the old path leaves the tree under the new name.
    """
    if not files:
        return False
    instruction_re = re.compile(instruction_pattern)
    for entry in files:
        status, separator, path = entry.partition("\t")
        if not separator:
            status, path = "", entry
        if status.startswith(("R", "C")) or status in NEVER_PROSE_STATUSES:
            return False
        if not path.endswith(".md"):
            return False
        if PROSE_SCOPE_RE.search(path) or instruction_re.search(path):
            return False
    return True


def triage(
    ledger: dict[str, Any],
    files: list[str],
    *,
    comments: int,
    forced: bool = False,
    volume_limit: int = LANE_VOLUME_LIMIT,
    instruction_pattern: str = INSTRUCTION_PATTERN,
) -> str | None:
    """Return the stand-down reason, or None when the paid lane may run.

    The order is binding: prose-only diff, then budget, then volume. An
    owner-forced round skips the prose and volume rules but never the
    budget.
    """
    if type(comments) is not int or comments < 0:
        raise SummaryError("The comment count must be a nonnegative integer.")
    if type(volume_limit) is not int or volume_limit < 1:
        raise SummaryError("The lane volume limit must be a positive integer.")
    if not forced and is_prose_only(files, instruction_pattern):
        return "the diff since the last verdict changes only prose outside the judged scope"
    if ledger["paid_rounds"] >= PAID_ROUND_LIMIT:
        return (
            f"the work item {ledger['work_item']} has spent "
            f"{ledger['paid_rounds']} paid rounds"
        )
    volume = len(open_threads(ledger)) + comments
    if not forced and volume > volume_limit:
        return f"{volume} open threads and comments exceed the lane volume limit of {volume_limit}"
    return None


def coverage_state(reason: str | None) -> str:
    return "paid" if reason is None else f"free lanes only: {reason}"


STANDDOWN_MARKER = "<!-- runeseer-standdown head={head} generation={generation} -->"


LEDGER_LINE_PREFIX = "ledger: "


def ledger_digest(ledger_bytes: bytes) -> str:
    """The sha256 of the ledger artifact, byte for byte as uploaded."""
    return hashlib.sha256(ledger_bytes).hexdigest()


def ledger_line(ledger_bytes: bytes, artifact_id: int, ledger: dict[str, Any] | None = None) -> str:
    """The first line of the `ledger` check run's output text.

    The seal verifiers read this line and nothing else from the check run:
    the merge-seal binds reviewed_sha, generation, and the digest. The
    artifact id lets a reader fetch the full ledger and prove it by the
    digest. Keys are sorted and the JSON is compact so the line is stable.
    """
    parsed = validate_ledger(ledger if ledger is not None else json.loads(ledger_bytes))
    if not isinstance(artifact_id, int) or artifact_id <= 0:
        raise SummaryError("A ledger line needs the positive artifact id of the uploaded ledger.")
    record = {
        "artifact_id": artifact_id,
        "digest": ledger_digest(ledger_bytes),
        "generation": parsed["generation"],
        "pull_request": parsed["pull_request"],
        "reviewed_sha": parsed["reviewed_sha"],
    }
    return LEDGER_LINE_PREFIX + json.dumps(record, sort_keys=True, separators=(",", ":"))


def standdown_notice(ledger: dict[str, Any]) -> str:
    """One owner-facing line for a stand-down, never a clean claim."""
    ledger = validate_ledger(ledger)
    coverage = ledger.get("coverage")
    if not isinstance(coverage, str) or not coverage.startswith("free lanes only"):
        raise SummaryError("A stand-down notice needs a free-lanes-only coverage state.")
    head = ledger["reviewed_sha"]
    marker = STANDDOWN_MARKER.format(head=head, generation=ledger["generation"])
    budget = ledger["paid_rounds"] >= PAID_ROUND_LIMIT
    action = (
        "The pull request waits for the owner."
        if budget
        else "Apply `review:runeseer` to force a paid round."
    )
    return (
        f"{marker}\n"
        f"review/correctness stood down on `{head[:8]}`: {coverage}. {action}"
    )


def validate_explicit_dispositions(
    value: Any, threads: list[dict[str, Any]]
) -> dict[str, tuple[str, str | None]]:
    """Explicit dispositions keyed by thread node id.

    An entry names its thread by `thread_id`, or by `comment_id` when the
    thread's first comment carries one. A thread without a comment id can
    still be disposed by its node id.
    """
    if value is None:
        return {}
    if not isinstance(value, list):
        raise SummaryError("The dispositions field must be an array.")
    by_comment = {
        thread["comment_id"]: thread["id"]
        for thread in threads
        if thread.get("comment_id") is not None
    }
    by_thread = {thread["id"] for thread in threads}
    explicit: dict[str, tuple[str, str | None]] = {}
    for item in value:
        if not isinstance(item, dict):
            raise SummaryError("Each disposition must be an object.")
        comment_id = item.get("comment_id")
        thread_id = item.get("thread_id")
        if thread_id is not None:
            if not isinstance(thread_id, str) or thread_id not in by_thread:
                raise SummaryError(
                    f"The disposition for thread {thread_id} names no open ledger thread."
                )
        elif type(comment_id) is not int or comment_id < 1:
            raise SummaryError("Each disposition needs a positive comment ID or a thread ID.")
        elif comment_id not in by_comment:
            raise SummaryError(
                f"The disposition for comment {comment_id} names no open ledger thread."
            )
        else:
            thread_id = by_comment[comment_id]
        disposition = item.get("disposition")
        if disposition not in DISPOSITIONS:
            raise SummaryError("Each disposition must be fixed, rejected, or owner.")
        reason = item.get("reason")
        if disposition == "rejected" or reason is not None:
            validate_plain_text(reason, "Each rejected disposition reason")
        explicit[thread_id] = (disposition, reason)
    return explicit


def derive_dispositions(
    verdict: dict[str, Any], ledger: dict[str, Any]
) -> list[dict[str, Any]]:
    """Dispose every open ledger thread from the verdict.

    Explicit `dispositions` entries win. A lane judgment then maps: already
    addressed is fixed, disputed is rejected with its reason, and a Low
    confirmed note is rejected as a note. A confirmed defect stays open.
    Runeseer's own thread is fixed when no open finding still names it.
    """
    explicit = validate_explicit_dispositions(
        verdict.get("dispositions"), open_threads(ledger)
    )
    judgments: dict[int, dict[str, Any]] = {}
    for judgment in verdict.get("lane_judgments", []):
        judgments.setdefault(judgment["comment_id"], judgment)
    open_finding_ids = {
        finding.get("comment_id")
        for finding in verdict.get("findings", [])
        if finding.get("comment_id") is not None
    }
    result: list[dict[str, Any]] = []
    for thread in open_threads(ledger):
        disposition: str | None = None
        reason: str | None = None
        comment_id = thread.get("comment_id")
        if thread["id"] in explicit:
            disposition, reason = explicit[thread["id"]]
        elif comment_id in open_finding_ids:
            disposition = None
        elif comment_id in judgments:
            judgment = judgments[comment_id]
            if judgment["judgment"] == "already addressed":
                disposition = "fixed"
            elif judgment["judgment"] == "disputed":
                disposition, reason = "rejected", judgment["reason"]
            elif judgment.get("severity") == "low":
                disposition, reason = "rejected", f"Low severity note: {judgment['reason']}"
        elif thread.get("lane") == "runeseer" and comment_id is not None:
            disposition = "fixed"
        result.append(
            {
                "id": thread["id"],
                "url": thread.get("url"),
                "comment_id": comment_id,
                "lane": thread.get("lane"),
                "disposition": disposition,
                "reason": reason,
            }
        )
    return result


def apply_verdict_to_ledger(
    ledger: dict[str, Any], verdict: dict[str, Any]
) -> dict[str, Any]:
    """Record the verdict's dispositions and its (sha, generation) binding."""
    ledger = validate_ledger(copy.deepcopy(ledger))
    if verdict.get("sha") != ledger["reviewed_sha"]:
        raise SummaryError("The verdict SHA does not match the ledger head.")
    if verdict.get("generation") != ledger["generation"]:
        raise SummaryError("The verdict generation does not match the ledger.")
    dispositions = {
        item["id"]: item for item in verdict.get("thread_dispositions", [])
    }
    for thread in ledger["threads"]:
        disposed = dispositions.get(thread["id"])
        if disposed is not None and disposed["disposition"] is not None:
            thread["disposition"] = disposed["disposition"]
            thread["reason"] = disposed.get("reason")
    ledger["coverage"] = "paid"
    # The paid lane's terminal status is the controller's own record: the
    # reviewer posts reviews, not check runs, so no check run reports it.
    if REVIEWER_LANE in ledger["lanes"]:
        own_threads = any(
            thread.get("lane") == REVIEWER_LANE for thread in ledger["threads"]
        )
        ledger["lanes"][REVIEWER_LANE] = (
            "completed"
            if verdict.get("findings") or own_threads
            else "completed-no-findings"
        )
    ledger["verdict"] = {
        "sha": verdict["sha"],
        "generation": verdict["generation"],
        "round": verdict.get("round"),
        "verdict": verdict["verdict"],
        "count": verdict.get("count"),
    }
    return ledger


def ledger_is_stale(ledger: dict[str, Any], thread_nodes: Any) -> bool:
    """A thread the ledger never saw stales its verdict and approval."""
    ledger = validate_ledger(ledger)
    known = {thread["id"] for thread in ledger["threads"]}
    if not isinstance(thread_nodes, list):
        raise SummaryError("The review threads must be an array.")
    for node in thread_nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise SummaryError("Each review thread needs a node id.")
        if node["id"] not in known:
            return True
    return False


def validate_verdict(
    verdict: Any,
    expected_sha: str,
    expected_round: int,
    lane_comments: list[dict[str, Any]] | None = None,
    runeseer_comments: list[dict[str, Any]] | None = None,
    previous_findings: list[dict[str, Any]] | None = None,
    runeseer_records: list[dict[str, Any]] | None = None,
    ledger: dict[str, Any] | None = None,
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
    if len(findings) > MAX_OPEN_FINDINGS:
        raise SummaryError(
            f"The verdict must not exceed {MAX_OPEN_FINDINGS} open findings."
        )
    if len(judgments) > MAX_LANE_JUDGMENTS:
        raise SummaryError(
            f"The verdict must not exceed {MAX_LANE_JUDGMENTS} lane judgments."
        )
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
        if item.get("lane") not in set(LANE_LOGINS.values()) | {"runeseer"}:
            raise SummaryError("Each finding needs a known source lane.")
        if item.get("source_kind", "comment") not in {"comment", "review"}:
            raise SummaryError("Each finding needs a known source kind.")
        if item.get("source_kind") == "review" and item.get("lane") == "runeseer":
            raise SummaryError("A Runeseer finding must identify an inline comment.")
        if item.get("judgment") != "confirmed":
            raise SummaryError("Each open finding needs a confirmed judgment.")
        comment_id = item.get("comment_id")
        if comment_id is not None and (type(comment_id) is not int or comment_id < 1):
            raise SummaryError("Each finding comment ID must be a positive integer.")
        if item["lane"] != "runeseer" and comment_id is None:
            raise SummaryError("Each lane finding needs its source comment ID.")
    for item in judgments:
        validate_lane_judgment(item)
    if lane_comments is not None:
        validate_lane_bindings(
            judgments, lane_comments, nonfinding_issue_ids, expected_sha
        )
    if runeseer_records is not None:
        validate_runeseer_records(findings, runeseer_records)
    if runeseer_comments is not None:
        validate_runeseer_bindings(
            findings,
            runeseer_comments,
            previous_findings or [],
            allow_unposted=runeseer_records is not None,
        )
    finding_keys = [finding_identity(item) for item in findings]
    if len(finding_keys) != len(set(finding_keys)):
        raise SummaryError("Each open finding needs a unique identity.")
    lane_findings = {
        key: item
        for key, item in zip(finding_keys, findings, strict=True)
        if key[0] != "runeseer"
    }
    confirmed = {
        finding_identity(item): item
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
    own_findings = {
        (item.get("path"), item.get("line"), item.get("summary")): item
        for item in findings
        if item.get("lane") == "runeseer"
    }
    confirmed_own = {
        (item.get("path"), item.get("line"), item.get("summary")): item
        for item in judgments
        if item.get("lane") == "runeseer"
        and item.get("judgment") == "confirmed"
        and item.get("severity") != "low"
    }
    if not confirmed_own.keys() <= own_findings.keys():
        raise SummaryError(
            "Each confirmed Runeseer judgment must remain an open Runeseer finding."
        )
    for key, judgment in confirmed_own.items():
        if own_findings[key]["severity"] != judgment["severity"]:
            raise SummaryError(
                "Each Runeseer finding must preserve its judgment severity."
            )
    expected_verdict = "clean" if not findings else "findings"
    if verdict["verdict"] != expected_verdict:
        raise SummaryError("The verdict value does not match the findings array.")
    generation = verdict.get("generation")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise SummaryError("The verdict generation must be a positive integer.")
    if ledger is not None:
        ledger = validate_ledger(ledger)
        if ledger["reviewed_sha"] != expected_sha:
            raise SummaryError("The ledger head does not match the reviewed head.")
        if generation != ledger["generation"]:
            raise SummaryError(
                "The verdict generation does not match the ledger generation."
            )
        dispositions = derive_dispositions(verdict, ledger)
        undisposed = [
            item for item in dispositions if item["disposition"] is None
        ]
        if verdict["verdict"] == "clean" and undisposed:
            first = undisposed[0]
            name = first.get("url") or first["id"]
            raise SummaryError(
                f"The clean verdict leaves a thread without a disposition: {name}"
            )
        verdict["thread_dispositions"] = dispositions
    return verdict


def prose_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9_'’./:-]*", text))


def validate_summary(summary: str, verdict: dict[str, Any]) -> str:
    summary = summary.strip()
    if not summary:
        raise SummaryError("The review summary is empty.")
    if "<!--" in summary or "-->" in summary:
        # A bare delimiter ban rejects every workflow marker: the verdict
        # marker, the failure notice, the owner escalation, and the legacy
        # forms. Model text must never look like a machine marker.
        raise SummaryError(
            "The model summary must not contain HTML comment delimiters."
        )
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
    if prose_word_count(summary) > 80 or utf8_size(summary) > SUMMARY_BYTE_LIMIT:
        summary = normalized_summary(verdict)
    return summary


def normalized_summary(verdict: dict[str, Any]) -> str:
    state = (
        "findings" if verdict["findings"] or verdict["restart"] != "none" else "clean"
    )
    parts: list[str] = []
    count = len(verdict["findings"])
    if count == 1:
        parts.append("The review found one blocking correctness defect.")
    elif count > 1:
        parts.append(f"The review found {count} blocking correctness defects.")
    else:
        parts.append("The review found no blocking correctness defects.")
    if verdict["restart"] != "none":
        reviewer = verdict["restart"].capitalize()
        parts.append(
            f"The workflow requires another {reviewer} review before approval."
        )
    return f"{VERDICT_PREFIXES[state]} {' '.join(parts)}"


def open_count_text(count: int) -> str:
    if count == 0:
        return "No open findings"
    if count == 1:
        return "1 open"
    return f"{count} open"


SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def headline(findings: list[dict[str, Any]]) -> str:
    count = len(findings)
    if count == 0:
        return "### Runeseer review — clean"
    noun = "finding" if count == 1 else "findings"
    return f"### Runeseer review — {count} open {noun}"


def findings_table(findings: list[dict[str, Any]]) -> list[str]:
    """Render the open findings as one risk table, highest severity first."""
    if not findings:
        return []
    ordered = sorted(
        findings,
        key=lambda item: (
            SEVERITY_RANK.get(str(item.get("severity")), len(SEVERITY_RANK)),
            str(item.get("path", "")),
            item.get("line") or 0,
        ),
    )
    show_source = any(item.get("lane") != "runeseer" for item in findings)
    lines = (
        ["| Risk | Finding | Location | Source |", "| --- | --- | --- | --- |"]
        if show_source
        else ["| Risk | Finding | Location |", "| --- | --- | --- |"]
    )
    for item in ordered:
        risk = str(item.get("severity", "")).capitalize()
        title = str(item.get("summary", "")).replace("|", "\\|")
        source = f" {LANE_NAMES[item['lane']]} |" if show_source else ""
        lines.append(f"| {risk} | {title} | `{item['path']}:{item['line']}` |{source}")
    return lines


def format_review(
    verdict_path: Path,
    summary_path: Path,
    expected_sha: str,
    expected_round: int,
    run_url: str,
    lane_comment_paths: list[Path] | None = None,
    runeseer_comment_paths: list[Path] | None = None,
    previous_verdict_path: Path | None = None,
    session_stats: str = "",
    runeseer_findings_path: Path | None = None,
    ledger_path: Path | None = None,
) -> str:
    lane_comments = load_lane_comments(lane_comment_paths)
    verdict_value = load_json(verdict_path)
    if lane_comments is not None:
        verdict_value = canonicalize_external_findings(verdict_value, lane_comments)
    verdict = validate_verdict(
        verdict_value,
        expected_sha,
        expected_round,
        lane_comments,
        load_lane_comments(runeseer_comment_paths),
        load_previous_findings(previous_verdict_path),
        load_runeseer_records(runeseer_findings_path),
        load_ledger(ledger_path),
    )
    try:
        summary_text = read_utf8(summary_path)
    except SummaryError:
        summary = normalized_summary(verdict)
    else:
        try:
            summary = validate_summary(summary_text, verdict)
        except SummaryError:
            summary = normalized_summary(verdict)
    try:
        verdict_path.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        raise SummaryError(f"Could not save {verdict_path}: {error}") from error
    footer_parts = [
        open_count_text(len(verdict["findings"])),
        f"Reviewed `{expected_sha[:8]}`",
        f"[review run]({run_url})",
    ]
    if session_stats:
        footer_parts.append(validate_plain_text(session_stats, "session stats"))
    footer = " · ".join(footer_parts)
    generation = verdict.get("generation")
    verdict_marker = VERDICT_MARKER.format(
        sha=expected_sha,
        base=verdict["base"],
        round=verdict["round"],
        verdict=verdict["verdict"],
        restart=verdict["restart"],
        generation="" if generation is None else f" generation={generation}",
    )
    lines = [
        SUMMARY_MARKER,
        verdict_marker,
        headline(verdict["findings"]),
        "",
    ]
    table = findings_table(verdict["findings"])
    if table:
        lines.extend(table)
        lines.append("")
    lines.extend((summary, "", "---", footer))
    body = "\n".join(lines)
    if utf8_size(body) <= MAX_GITHUB_COMMENT_BYTES:
        return body

    compact = "\n".join(
        (
            SUMMARY_MARKER,
            verdict_marker,
            headline(verdict["findings"]),
            "",
            normalized_summary(verdict),
            "",
            REVIEW_SIZE_FALLBACK,
            "",
            "---",
            footer,
        )
    )
    return validate_comment_body(compact, "The review summary")


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
        "generation": (
            int(match.group("generation")) if match.group("generation") else None
        ),
    }


def parse_canonical_summary_marker(body: str) -> dict[str, Any] | None:
    """Return the verdict identity from one canonical leading marker pair."""
    matches = list(VERDICT_MARKER_RE.finditer(body))
    if (
        body.count(SUMMARY_MARKER) != 1
        or body.count("<!-- runeseer-verdict") != 1
        or len(matches) != 1
    ):
        return None
    match = matches[0]
    if not body.startswith(f"{SUMMARY_MARKER}\n{match.group(0)}\n"):
        return None
    return {
        "sha": match.group("sha"),
        "base": match.group("base"),
        "round": int(match.group("round")),
        "verdict": match.group("verdict"),
        "restart": match.group("restart") or "none",
        "generation": (
            int(match.group("generation")) if match.group("generation") else None
        ),
    }


def marker_round(body: str, base: str | None = None, dispatches: bool = False) -> int:
    """The highest round the reviewer recorded in this comment history.

    With `dispatches`, the round-start markers count too: a round voided
    after the model call still spent the budget, and the count is not
    keyed to a base, so a moved base never restarts it.
    """
    rounds = [
        int(match.group("round"))
        for match in VERDICT_MARKER_RE.finditer(body)
        if base is None or match.group("base") == base
    ]
    if dispatches:
        rounds.extend(
            int(match.group("round")) for match in ROUND_START_RE.finditer(body)
        )
    return max(rounds, default=0)


def round_start_marker(sha: str, base: str, round_number: int, generation: int) -> str:
    """The dispatch record the lane posts before the model call."""
    for value in (sha, base):
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise SummaryError("The round-start marker needs full commit SHAs.")
    if type(round_number) is not int or round_number < 1:
        raise SummaryError("The round-start marker needs a positive round.")
    if type(generation) is not int or generation < 1:
        raise SummaryError("The round-start marker needs a positive generation.")
    marker = ROUND_START_MARKER.format(sha=sha, base=base, round=round_number)
    return (
        f"{marker}\n"
        f"review/correctness round {round_number} started on `{sha[:8]}` "
        f"at ledger generation {generation}."
    )


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
    # Within one round, the formatter-owned footer run outranks every
    # timestamp: an older run can rewrite a comment after a newer run.
    current = max(
        candidates,
        key=lambda comment: (
            marker_round(comment.get("body", ""), base),
            footer_run_id(comment.get("body", "")),
            comment.get("updated_at", comment.get("created_at", "")),
            comment.get("id", 0),
        ),
    )
    if not isinstance(current.get("id"), int):
        raise SummaryError("The existing summary comment has no numeric ID.")
    return current


def footer_run_id(body: str) -> int:
    """Return the workflow run from the formatter-owned final footer."""
    _, separator, footer = body.rpartition("\n---\n")
    if not separator:
        return 0
    match = REVIEW_FOOTER_RE.fullmatch(footer.strip())
    return int(match.group("run")) if match is not None else 0


def latest_summary_clock(
    repo: str, number: int, author: str, head: str, base: str
) -> tuple[int, int]:
    """Return the newest round and workflow run for this head and base."""
    endpoint = f"repos/{repo}/issues/{number}/comments?per_page=100"
    result = run_gh(["api", "--paginate", endpoint, "--jq", ".[] | @json"])
    if result.returncode != 0:
        raise SummaryError(
            f"Could not read pull request comments: {result.stderr.strip()}"
        )
    clocks: list[tuple[int, int]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            comment = json.loads(line)
        except json.JSONDecodeError as error:
            raise SummaryError("GitHub returned an invalid comment record.") from error
        user = comment.get("user") if isinstance(comment, dict) else None
        login = user.get("login") if isinstance(user, dict) else None
        body = comment.get("body") if isinstance(comment, dict) else None
        if login != author or not isinstance(body, str) or SUMMARY_MARKER not in body:
            continue
        verdict_marker = parse_verdict_marker(body)
        if (
            verdict_marker is None
            or verdict_marker["sha"] != head
            or verdict_marker["base"] != base
        ):
            continue
        clocks.append((verdict_marker["round"], footer_run_id(body)))
    return max(clocks, default=(0, 0))


def summary_comment_clock(
    repo: str, number: int, comment_id: int, author: str, head: str, base: str
) -> tuple[int, int]:
    """Read one trusted summary comment and return its round and run.

    The direct read replaces a full comment pagination after publication.
    Every validation failure raises, so a vanished or altered summary
    stops the mutation instead of allowing it.
    """
    result = run_gh(["api", f"repos/{repo}/issues/comments/{comment_id}"])
    if result.returncode != 0:
        raise SummaryError(
            f"Could not read the review summary comment: {result.stderr.strip()}"
        )
    try:
        comment = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SummaryError("GitHub returned an invalid comment record.") from error
    if not isinstance(comment, dict) or comment.get("id") != comment_id:
        raise SummaryError("The target is no longer the review summary comment.")
    expected_issue_url = f"https://api.github.com/repos/{repo}/issues/{number}"
    if comment.get("issue_url") != expected_issue_url:
        raise SummaryError("The review summary comment belongs to another issue.")
    user = comment.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    body = comment.get("body")
    if login != author or not isinstance(body, str):
        raise SummaryError("The target is no longer the review summary comment.")
    verdict_marker = parse_canonical_summary_marker(body)
    if verdict_marker is None or verdict_marker["round"] < 1:
        raise SummaryError(
            "The review summary comment needs exactly one canonical marker pair."
        )
    if verdict_marker["sha"] != head or verdict_marker["base"] != base:
        raise SummaryError("The review summary comment reviews another head or base.")
    _, separator, footer = body.rpartition("\n---\n")
    footer_match = REVIEW_FOOTER_RE.fullmatch(footer.strip()) if separator else None
    if footer_match is None:
        raise SummaryError("The review summary comment has no footer run.")
    if footer_match.group("sha") != head[:8]:
        raise SummaryError("The review summary footer reviews another head.")
    run = int(footer_match.group("run"))
    return verdict_marker["round"], run


def published_summary_is_newer(
    repo: str,
    number: int,
    author: str,
    head: str,
    base: str,
    review_round: int,
    run_id: int,
    summary_comment_id: int | None = None,
) -> bool:
    """Return true when a newer published summary supersedes this run."""
    if summary_comment_id is not None:
        latest_round, latest_run = summary_comment_clock(
            repo, number, summary_comment_id, author, head, base
        )
    else:
        latest_round, latest_run = latest_summary_clock(
            repo, number, author, head, base
        )
    return latest_round > review_round or (
        latest_round == review_round and latest_run > run_id
    )


def publish_summary(body: str, repo: str, number: int, author: str) -> tuple[str, int]:
    validate_comment_body(body, "The review summary")
    marker = parse_verdict_marker(body)
    if marker is None or marker["round"] < 1:
        raise SummaryError("The review summary has no valid verdict marker.")
    comment = find_summary_comment(repo, number, author, marker["base"])
    comment_id = comment.get("id") if comment else None
    comment_identity = (
        parse_canonical_summary_marker(comment.get("body", "")) if comment else None
    )
    if comment is not None and comment_identity is None:
        raise SummaryError("The existing review summary has no canonical marker pair.")
    existing_round = (
        marker_round(comment.get("body", ""), marker["base"]) if comment else 0
    )
    if existing_round > marker["round"]:
        raise SummaryError("A current or newer review summary already exists.")
    if existing_round == marker["round"]:
        # Within one round, the workflow run in the formatter-owned footer is
        # the race authority: a newer run can replace the summary, and an
        # equal or older run cannot.
        existing_run = footer_run_id(comment.get("body", "")) if comment else 0
        if footer_run_id(body) <= existing_run:
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
    if comment_id is not None:
        # A newer run can publish between the first read and this update,
        # and a duplicate comment can hide that run behind the selected
        # comment. A fresh discovery re-reads the selected comment and
        # revalidates its round and run before the update.
        fresh = find_summary_comment(repo, number, author, marker["base"])
        fresh_identity = (
            parse_canonical_summary_marker(fresh.get("body", "")) if fresh else None
        )
        if (
            fresh is None
            or fresh.get("id") != comment_id
            or fresh_identity is None
            or fresh_identity != comment_identity
        ):
            raise SummaryError(
                "The review summary marker identity changed before publication."
            )
        fresh_round = marker_round(fresh.get("body", ""), marker["base"])
        if fresh_round > marker["round"] or (
            fresh_round == marker["round"]
            and footer_run_id(fresh.get("body", "")) >= footer_run_id(body)
        ):
            raise SummaryError("A current or newer review summary already exists.")
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
    response_id = response.get("id") if isinstance(response, dict) else None
    if type(response_id) is not int:
        raise SummaryError("GitHub returned an invalid publication record.")
    if comment_id is not None and response_id != comment_id:
        raise SummaryError("GitHub updated a different summary comment.")
    if response.get("body") != body:
        raise SummaryError("GitHub did not store the complete review summary.")
    return action, response_id


def classify_failure(
    verdict_exists: bool,
    required_outputs_exist: bool,
    verdict_valid: bool,
    formatter_outcome: str,
    adjudicate_outcome: str,
    prevalidate_outcome: str,
    prepare_outcome: str,
    publish_outcome: str,
    pre_breaker_status: str,
    judgments_outcome: str,
    upload_outcome: str,
    consume_outcome: str,
    restart_outcome: str,
) -> str | None:
    """Return the operational failure kind, or return None after success."""
    for name, outcome in (
        ("formatter", formatter_outcome),
        ("adjudicate", adjudicate_outcome),
        ("prevalidate", prevalidate_outcome),
        ("prepare", prepare_outcome),
        ("publish", publish_outcome),
        ("judgments", judgments_outcome),
        ("upload", upload_outcome),
        ("consume", consume_outcome),
        ("restart", restart_outcome),
    ):
        if outcome not in STEP_OUTCOMES:
            raise SummaryError(f"The {name} step outcome is invalid: {outcome}")
    if pre_breaker_status not in JOB_STATUSES:
        raise SummaryError(
            f"The pre-breaker job status is invalid: {pre_breaker_status}"
        )
    if verdict_valid and not verdict_exists:
        raise SummaryError("An absent verdict cannot pass validation.")
    if required_outputs_exist and not verdict_exists:
        raise SummaryError("Required review output cannot omit the verdict.")

    # GitHub cancels a round when a newer run replaces it. Cancellation does
    # not describe a review failure and must not create a failure notice.
    if pre_breaker_status == "cancelled" or adjudicate_outcome == "cancelled":
        return None

    if adjudicate_outcome == "failure":
        return "provider"
    if formatter_outcome != "success" or adjudicate_outcome != "success":
        return "workflow"
    if not required_outputs_exist:
        return "missing"
    if prevalidate_outcome == "failure":
        return "invalid"
    if prevalidate_outcome != "success":
        return "workflow_after_review"
    # A prior workflow failure can skip publication and leave an unbound
    # verdict. Report that workflow failure before any verdict defect.
    if prepare_outcome in {"skipped", "cancelled"}:
        return "workflow_after_review"
    if prepare_outcome == "failure" or not verdict_valid:
        return "invalid"
    if publish_outcome in {"skipped", "cancelled"}:
        return "workflow_after_review"
    if publish_outcome == "failure":
        return "publication"
    if publish_outcome != "success":
        return "workflow_after_review"
    finalizer_outcomes = (
        judgments_outcome,
        upload_outcome,
        consume_outcome,
        restart_outcome,
    )
    if pre_breaker_status != "success" or any(
        outcome != "success" for outcome in finalizer_outcomes
    ):
        return "workflow_after_publication"
    return None


def failure_notice(kind: str, run_url: str, head: str, base: str, run_id: int) -> str:
    """Render a fixed failure notice for one reviewed head."""
    if kind not in FAILURE_NOTICES:
        raise SummaryError(f"The failure notice kind is invalid: {kind}")
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise SummaryError("The failure notice needs a full commit SHA.")
    if re.fullmatch(r"[0-9a-f]{40}", base) is None:
        raise SummaryError("The failure notice needs a full base SHA.")
    if type(run_id) is not int or run_id < 1:
        raise SummaryError("The failure notice needs a positive run ID.")
    validate_plain_text(run_url, "The failure notice run URL")
    title, detail, action = FAILURE_NOTICES[kind]
    marker = (
        f"<!-- runeseer-failure-notice head={head} base={base} "
        f"run={run_id} stage={kind} -->"
    )
    body = "\n\n".join(
        (
            title,
            detail,
            action,
            FAILURE_RETRY_GUIDANCE,
            f"Affected head: `{head[:8]}`.",
            f"[Open the failed workflow]({run_url})",
            marker,
        )
    )
    return validate_comment_body(body, "The failure notice")


def verify_live_pull_request(repo: str, number: int, head: str, base: str) -> None:
    """Require the expected live pull request before a remote mutation."""
    result = run_gh(
        ["api", f"repos/{repo}/pulls/{number}", "--jq", "[.head.sha, .base.sha] | @tsv"]
    )
    if result.returncode != 0:
        raise SummaryError(
            f"Could not verify the live pull request: {result.stderr.strip()}"
        )
    if result.stdout.strip() != f"{head}\t{base}":
        raise SummaryError("The pull request changed before the notice mutation.")


def transient_notice_spec(
    notice_type: str,
) -> tuple[re.Pattern[str], re.Pattern[str], str]:
    """Return the current marker, legacy marker, and public notice name."""
    try:
        return TRANSIENT_NOTICE_MARKERS[notice_type]
    except KeyError as error:
        raise SummaryError(
            f"The transient notice type is invalid: {notice_type}"
        ) from error


def transient_notice_run(body: str, notice_type: str) -> int | None:
    """Return the notice run ID, with legacy notices assigned to run zero."""
    marker_re, legacy_re, _ = transient_notice_spec(notice_type)
    marker = marker_re.search(body)
    if marker is not None:
        return int(marker.group("run"))
    if legacy_re.search(body) is not None:
        return 0
    if notice_type == "owner" and body.startswith(LEGACY_OWNER_ESCALATION_PREFIX):
        return 0
    return None


def parse_transient_comment(
    value: Any, author: str, notice_type: str
) -> dict[str, Any] | None:
    """Validate one bot-authored transient Runeseer notice."""
    _, _, notice_name = transient_notice_spec(notice_type)
    if not isinstance(value, dict):
        raise SummaryError("GitHub returned an invalid comment record.")
    user = value.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    body = value.get("body")
    if isinstance(body, str) and (
        SUMMARY_MARKER in body or VERDICT_MARKER_RE.search(body) is not None
    ):
        # The review summary comment is permanent. A transient-notice marker
        # inside it must never let a reconciler edit or delete the summary.
        return None
    run_id = transient_notice_run(body, notice_type) if isinstance(body, str) else None
    if login != author or run_id is None:
        return None
    if type(value.get("id")) is not int:
        raise SummaryError(f"A Runeseer {notice_name} has no numeric ID.")
    return {**value, "_runeseer_transient_run": run_id}


def find_transient_comments(
    repo: str, number: int, author: str, notice_type: str
) -> list[dict[str, Any]]:
    """Return bot-authored current and legacy transient notices."""
    endpoint = (
        f"repos/{repo}/issues/{number}/comments?per_page=100"
        "&sort=created&direction=desc"
    )
    result = run_gh(["api", "--paginate", endpoint, "--jq", ".[] | @json"])
    if result.returncode != 0:
        raise SummaryError(
            f"Could not read pull request comments: {result.stderr.strip()}"
        )
    comments: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            comment = json.loads(line)
        except json.JSONDecodeError as error:
            raise SummaryError("GitHub returned an invalid comment record.") from error
        parsed = parse_transient_comment(comment, author, notice_type)
        if parsed is not None:
            comments.append(parsed)
    return comments


def read_transient_comment(
    repo: str, comment_id: int, author: str, notice_type: str
) -> dict[str, Any]:
    """Re-read and validate a transient notice before its mutation."""
    _, _, notice_name = transient_notice_spec(notice_type)
    result = run_gh(["api", f"repos/{repo}/issues/comments/{comment_id}"])
    if result.returncode != 0:
        raise SummaryError(
            f"Could not re-read the {notice_name}: {result.stderr.strip()}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SummaryError("GitHub returned an invalid comment record.") from error
    comment = parse_transient_comment(value, author, notice_type)
    if comment is None or comment["id"] != comment_id:
        raise SummaryError(f"The target is no longer a Runeseer {notice_name}.")
    return comment


def reconcile_transient_comment(
    body: str | None,
    repo: str,
    number: int,
    author: str,
    head: str,
    base: str,
    review_round: int,
    run_id: int,
    notice_type: str,
    summary_comment_id: int | None = None,
) -> str:
    """Create, update, or remove one transient Runeseer comment."""
    marker_re, _, notice_name = transient_notice_spec(notice_type)
    if type(run_id) is not int or run_id < 1:
        raise SummaryError(f"The {notice_name} needs a positive run ID.")
    if type(review_round) is not int or review_round < 1:
        raise SummaryError(f"The {notice_name} needs a positive review round.")
    if body is not None:
        validate_comment_body(body, f"The Runeseer {notice_name}")
        markers = list(marker_re.finditer(body))
        if len(markers) != 1:
            raise SummaryError(f"The Runeseer {notice_name} needs one sticky marker.")
        marker = markers[0]
        if (
            marker.group("head") != head
            or marker.group("base") != base
            or int(marker.group("run")) != run_id
        ):
            raise SummaryError(f"The {notice_name} marker does not match this run.")
        if notice_type == "failure" and marker.group("stage") not in FAILURE_NOTICES:
            raise SummaryError("The failure notice marker has an invalid stage.")

    def superseded() -> bool:
        """Compare this run against the published summary right now.

        Every mutation calls this again. A trusted summary comment ID
        turns each comparison into one direct comment read; without one,
        the comparison walks the full comment discovery. No mutation
        reuses an earlier comparison result.
        """
        return published_summary_is_newer(
            repo,
            number,
            author,
            head,
            base,
            review_round,
            run_id,
            summary_comment_id,
        )

    if superseded():
        return "kept newer"
    comments = find_transient_comments(repo, number, author, notice_type)
    if any(comment["_runeseer_transient_run"] > run_id for comment in comments):
        return "kept newer"
    if body is None:
        for comment in comments:
            fresh = read_transient_comment(repo, comment["id"], author, notice_type)
            if fresh["_runeseer_transient_run"] > run_id:
                return "kept newer"
            if superseded():
                return "kept newer"
            verify_live_pull_request(repo, number, head, base)
            result = run_gh(
                ["api", "-X", "DELETE", f"repos/{repo}/issues/comments/{comment['id']}"]
            )
            if result.returncode != 0:
                raise SummaryError(
                    f"Could not remove the stale {notice_name}: {result.stderr.strip()}"
                )
        return f"removed {len(comments)}"

    current = max(
        comments,
        key=lambda comment: (
            comment["_runeseer_transient_run"],
            comment.get("updated_at", comment.get("created_at", "")),
            comment["id"],
        ),
        default=None,
    )
    if current is None:
        if superseded():
            return "kept newer"
        verify_live_pull_request(repo, number, head, base)
        arguments = ["api", "-X", "POST", f"repos/{repo}/issues/{number}/comments"]
        action = "created"
    else:
        fresh = read_transient_comment(repo, current["id"], author, notice_type)
        if fresh["_runeseer_transient_run"] > run_id:
            return "kept newer"
        if superseded():
            return "kept newer"
        verify_live_pull_request(repo, number, head, base)
        arguments = [
            "api",
            "-X",
            "PATCH",
            f"repos/{repo}/issues/comments/{current['id']}",
        ]
        action = "updated"
    result = run_gh([*arguments, "-f", f"body={body}"])
    if result.returncode != 0:
        raise SummaryError(
            f"Could not {action.removesuffix('d')} the {notice_name}: "
            f"{result.stderr.strip()}"
        )
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SummaryError(
            "GitHub returned an invalid transient notice record."
        ) from error
    if current is not None and response.get("id") != current["id"]:
        raise SummaryError(f"GitHub updated a different {notice_name}.")
    if response.get("body") != body:
        raise SummaryError(f"GitHub did not store the complete {notice_name}.")

    for comment in comments:
        if current is not None and comment["id"] == current["id"]:
            continue
        fresh = read_transient_comment(repo, comment["id"], author, notice_type)
        if fresh["_runeseer_transient_run"] > run_id:
            raise SummaryError(f"A newer {notice_name} replaced a duplicate comment.")
        if superseded():
            return "kept newer"
        verify_live_pull_request(repo, number, head, base)
        result = run_gh(
            ["api", "-X", "DELETE", f"repos/{repo}/issues/comments/{comment['id']}"]
        )
        if result.returncode != 0:
            raise SummaryError(
                f"Could not remove a duplicate {notice_name}: {result.stderr.strip()}"
            )
    return action


def reconcile_failure_notice(
    body: str | None,
    repo: str,
    number: int,
    author: str,
    head: str,
    base: str,
    review_round: int,
    run_id: int,
    summary_comment_id: int | None = None,
) -> str:
    """Reconcile the single Runeseer failure notice."""
    return reconcile_transient_comment(
        body,
        repo,
        number,
        author,
        head,
        base,
        review_round,
        run_id,
        "failure",
        summary_comment_id,
    )


def reconcile_owner_escalation(
    body: str | None,
    repo: str,
    number: int,
    author: str,
    head: str,
    base: str,
    review_round: int,
    run_id: int,
    summary_comment_id: int | None = None,
) -> str:
    """Reconcile the single Runeseer owner escalation."""
    return reconcile_transient_comment(
        body,
        repo,
        number,
        author,
        head,
        base,
        review_round,
        run_id,
        "owner",
        summary_comment_id,
    )


def write_output(body: str, output: Path | None) -> None:
    if output is None:
        print(body)
        return
    try:
        output.write_text(body + "\n", encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SummaryError(
            f"Could not write valid UTF-8 to {output}: {error}"
        ) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    # The lane table replaces the built-in lane maps for every command.
    parser.add_argument("--lane-table", type=Path, dest="lane_table_global")
    commands = parser.add_subparsers(dest="command", required=True)

    collect_parser = commands.add_parser("collect-lanes")
    collect_parser.add_argument("--inline", type=Path, required=True)
    collect_parser.add_argument("--issues", type=Path, required=True)
    collect_parser.add_argument("--reviews", type=Path, required=True)
    collect_parser.add_argument("--head", required=True)
    collect_parser.add_argument("--previous-verdict", type=Path)
    collect_parser.add_argument("--output-dir", type=Path, required=True)

    thread_parser = commands.add_parser("match-lane-thread")
    thread_parser.add_argument("--judgment", type=Path, required=True)
    thread_parser.add_argument("--threads", type=Path, required=True)

    round_parser = commands.add_parser("next-round")
    round_parser.add_argument("--previous-verdict", type=Path)
    round_parser.add_argument("--legacy-rounds", type=int, required=True)
    round_parser.add_argument("--marker-round", type=int, required=True)

    marker_parser = commands.add_parser("marker-round")
    marker_parser.add_argument("--history", type=Path, required=True)
    marker_parser.add_argument("--base")
    marker_parser.add_argument("--dispatches", action="store_true")

    start_parser = commands.add_parser("round-start")
    start_parser.add_argument("--sha", required=True)
    start_parser.add_argument("--base", required=True)
    start_parser.add_argument("--round", type=int, required=True)
    start_parser.add_argument("--generation", type=int, required=True)

    owner_parser = commands.add_parser("owner-from-codeowners")
    owner_parser.add_argument("--codeowners", type=Path, required=True)

    escalation_parser = commands.add_parser("owner-escalation")
    escalation_parser.add_argument("--owner", required=True)
    escalation_parser.add_argument("--findings", type=Path, required=True)
    escalation_parser.add_argument("--head", required=True)
    escalation_parser.add_argument("--base", required=True)
    escalation_parser.add_argument("--run-id", type=int, required=True)

    failure_kind_parser = commands.add_parser("failure-kind")
    failure_kind_parser.add_argument(
        "--verdict-exists", choices=("true", "false"), required=True
    )
    failure_kind_parser.add_argument(
        "--required-outputs-exist", choices=("true", "false"), required=True
    )
    failure_kind_parser.add_argument(
        "--verdict-valid", choices=("true", "false"), required=True
    )
    failure_kind_parser.add_argument(
        "--formatter-outcome", choices=sorted(STEP_OUTCOMES), required=True
    )
    failure_kind_parser.add_argument(
        "--adjudicate-outcome", choices=sorted(STEP_OUTCOMES), required=True
    )
    failure_kind_parser.add_argument(
        "--prevalidate-outcome", choices=sorted(STEP_OUTCOMES), required=True
    )
    failure_kind_parser.add_argument(
        "--prepare-outcome", choices=sorted(STEP_OUTCOMES), required=True
    )
    failure_kind_parser.add_argument(
        "--publish-outcome", choices=sorted(STEP_OUTCOMES), required=True
    )
    failure_kind_parser.add_argument(
        "--pre-breaker-status", choices=sorted(JOB_STATUSES), required=True
    )
    failure_kind_parser.add_argument(
        "--judgments-outcome", choices=sorted(STEP_OUTCOMES), required=True
    )
    failure_kind_parser.add_argument(
        "--upload-outcome", choices=sorted(STEP_OUTCOMES), required=True
    )
    failure_kind_parser.add_argument(
        "--consume-outcome", choices=sorted(STEP_OUTCOMES), required=True
    )
    failure_kind_parser.add_argument(
        "--restart-outcome", choices=sorted(STEP_OUTCOMES), required=True
    )

    failure_parser = commands.add_parser("failure-notice")
    failure_parser.add_argument(
        "--kind", choices=sorted(FAILURE_NOTICES), required=True
    )
    failure_parser.add_argument("--run-url", required=True)
    failure_parser.add_argument("--head", required=True)
    failure_parser.add_argument("--base", required=True)
    failure_parser.add_argument("--run-id", type=int, required=True)

    reconcile_parser = commands.add_parser("reconcile-failure-notice")
    body_group = reconcile_parser.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body", type=Path)
    body_group.add_argument("--clear", action="store_true")
    reconcile_parser.add_argument("--repo", required=True)
    reconcile_parser.add_argument("--pr", type=int, required=True)
    reconcile_parser.add_argument("--author", default="runeseer[bot]")
    reconcile_parser.add_argument("--head", required=True)
    reconcile_parser.add_argument("--base", required=True)
    reconcile_parser.add_argument("--review-round", type=int, required=True)
    reconcile_parser.add_argument("--run-id", type=int, required=True)
    reconcile_parser.add_argument("--summary-comment-id", type=int)

    owner_reconcile_parser = commands.add_parser("reconcile-owner-escalation")
    owner_body_group = owner_reconcile_parser.add_mutually_exclusive_group(
        required=True
    )
    owner_body_group.add_argument("--body", type=Path)
    owner_body_group.add_argument("--clear", action="store_true")
    owner_reconcile_parser.add_argument("--repo", required=True)
    owner_reconcile_parser.add_argument("--pr", type=int, required=True)
    owner_reconcile_parser.add_argument("--author", default="runeseer[bot]")
    owner_reconcile_parser.add_argument("--head", required=True)
    owner_reconcile_parser.add_argument("--base", required=True)
    owner_reconcile_parser.add_argument("--review-round", type=int, required=True)
    owner_reconcile_parser.add_argument("--run-id", type=int, required=True)
    owner_reconcile_parser.add_argument("--summary-comment-id", type=int)

    newer_parser = commands.add_parser("newer-summary")
    newer_parser.add_argument("--repo", required=True)
    newer_parser.add_argument("--pr", type=int, required=True)
    newer_parser.add_argument("--author", default="runeseer[bot]")
    newer_parser.add_argument("--head", required=True)
    newer_parser.add_argument("--base", required=True)
    newer_parser.add_argument("--review-round", type=int, required=True)
    newer_parser.add_argument("--run-id", type=int, required=True)
    newer_parser.add_argument("--summary-comment-id", type=int)

    validate_parser = commands.add_parser("validate-verdict")
    validate_parser.add_argument("--verdict", type=Path, required=True)
    validate_parser.add_argument("--sha", required=True)
    validate_parser.add_argument("--round", type=int, required=True)
    validate_parser.add_argument("--lane-comments", type=Path, action="append")
    validate_parser.add_argument("--runeseer-comments", type=Path, action="append")
    validate_parser.add_argument("--previous-verdict", type=Path)
    validate_parser.add_argument("--runeseer-findings", type=Path)
    validate_parser.add_argument("--ledger", type=Path)

    build_parser_ = commands.add_parser("build-ledger")
    build_parser_.add_argument("--pr", type=int, required=True)
    build_parser_.add_argument("--head", required=True)
    build_parser_.add_argument("--base", required=True)
    build_parser_.add_argument("--lane-table", type=Path, required=True)
    build_parser_.add_argument("--threads", type=Path, required=True)
    build_parser_.add_argument("--check-runs", type=Path, required=True)
    build_parser_.add_argument("--labels", type=Path, required=True)
    build_parser_.add_argument("--body", type=Path, required=True)
    build_parser_.add_argument("--previous-ledger", type=Path)
    build_parser_.add_argument("--paid-rounds", type=int, default=0)
    build_parser_.add_argument(
        "--lane-status", action="append", default=[], metavar="LANE=STATUS"
    )
    build_parser_.add_argument("--output", type=Path)

    triage_parser = commands.add_parser("triage")
    triage_parser.add_argument("--ledger", type=Path, required=True)
    triage_parser.add_argument("--files", type=Path, required=True)
    triage_parser.add_argument("--comments", type=int, required=True)
    triage_parser.add_argument("--forced", action="store_true")
    triage_parser.add_argument("--volume-limit", type=int, default=LANE_VOLUME_LIMIT)
    triage_parser.add_argument("--instruction-pattern", default=INSTRUCTION_PATTERN)
    triage_parser.add_argument("--output", type=Path)

    apply_parser = commands.add_parser("apply-verdict")
    apply_parser.add_argument("--ledger", type=Path, required=True)
    apply_parser.add_argument("--verdict", type=Path, required=True)
    apply_parser.add_argument("--output", type=Path)

    stale_parser = commands.add_parser("ledger-stale")
    stale_parser.add_argument("--ledger", type=Path, required=True)
    stale_parser.add_argument("--threads", type=Path, required=True)

    work_item_parser = commands.add_parser("work-item")
    work_item_parser.add_argument("--body", type=Path, required=True)
    work_item_parser.add_argument("--pr", type=int, required=True)

    standdown_parser = commands.add_parser("standdown-notice")
    standdown_parser.add_argument("--ledger", type=Path, required=True)

    ledger_line_parser = commands.add_parser("ledger-line")
    ledger_line_parser.add_argument("--ledger", type=Path, required=True)
    ledger_line_parser.add_argument("--artifact-id", type=int, required=True)

    ledger_parser = commands.add_parser("read-ledger")
    ledger_parser.add_argument("--verdict", type=Path, required=True)
    ledger_parser.add_argument("--sha", required=True)
    ledger_parser.add_argument("--round", type=int, required=True)
    ledger_parser.add_argument("--output", type=Path)

    format_parser = commands.add_parser("format")
    format_parser.add_argument("--verdict", type=Path, required=True)
    format_parser.add_argument("--summary", type=Path, required=True)
    format_parser.add_argument("--sha", required=True)
    format_parser.add_argument("--round", type=int, required=True)
    format_parser.add_argument("--run-url", required=True)
    format_parser.add_argument("--lane-comments", type=Path, action="append")
    format_parser.add_argument("--runeseer-comments", type=Path, action="append")
    format_parser.add_argument("--previous-verdict", type=Path)
    format_parser.add_argument("--runeseer-findings", type=Path)
    format_parser.add_argument("--session-stats", default="")
    format_parser.add_argument("--ledger", type=Path)
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
        if arguments.lane_table_global is not None:
            configure_lanes(load_lane_table(load_json(arguments.lane_table_global)))
        if arguments.command == "round-start":
            print(
                round_start_marker(
                    arguments.sha, arguments.base, arguments.round, arguments.generation
                )
            )
            return 0
        if arguments.command == "collect-lanes":
            inline = load_json(arguments.inline)
            issues = load_json(arguments.issues)
            previous = load_previous_findings(arguments.previous_verdict)
            evidence = collect_lane_evidence(
                inline,
                issues,
                load_json(arguments.reviews),
                arguments.head,
                previous,
            )
            arguments.output_dir.mkdir(parents=True, exist_ok=True)
            for name, records in evidence.items():
                (arguments.output_dir / f"{name}.json").write_text(
                    json.dumps(records, indent=2) + "\n", encoding="utf-8"
                )
            context = collect_rebuttal_context(
                inline, issues, arguments.head, evidence["inline-comments"], previous
            )
            (arguments.output_dir / "rebuttal-context.json").write_text(
                json.dumps(context, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            return 0
        if arguments.command == "match-lane-thread":
            thread = matching_lane_thread(
                load_json(arguments.judgment), load_json(arguments.threads)
            )
            if thread is not None:
                print(json.dumps(thread))
            return 0
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
                    read_utf8(arguments.history), arguments.base, arguments.dispatches
                )
            )
            return 0
        if arguments.command == "owner-from-codeowners":
            print(repository_owner(read_utf8(arguments.codeowners)))
            return 0
        if arguments.command == "owner-escalation":
            print(
                format_owner_escalation(
                    arguments.owner,
                    load_json(arguments.findings),
                    arguments.head,
                    arguments.base,
                    arguments.run_id,
                )
            )
            return 0
        if arguments.command == "failure-kind":
            kind = classify_failure(
                arguments.verdict_exists == "true",
                arguments.required_outputs_exist == "true",
                arguments.verdict_valid == "true",
                arguments.formatter_outcome,
                arguments.adjudicate_outcome,
                arguments.prevalidate_outcome,
                arguments.prepare_outcome,
                arguments.publish_outcome,
                arguments.pre_breaker_status,
                arguments.judgments_outcome,
                arguments.upload_outcome,
                arguments.consume_outcome,
                arguments.restart_outcome,
            )
            print(kind or "none")
            return 0
        if arguments.command == "failure-notice":
            print(
                failure_notice(
                    arguments.kind,
                    arguments.run_url,
                    arguments.head,
                    arguments.base,
                    arguments.run_id,
                )
            )
            return 0
        if arguments.command == "reconcile-failure-notice":
            body = (
                read_utf8(arguments.body).rstrip("\n")
                if arguments.body is not None
                else None
            )
            action = reconcile_failure_notice(
                body,
                arguments.repo,
                arguments.pr,
                arguments.author,
                arguments.head,
                arguments.base,
                arguments.review_round,
                arguments.run_id,
                arguments.summary_comment_id,
            )
            if action.startswith("removed "):
                count = int(action.split()[1])
                noun = "notice" if count == 1 else "notices"
                print(f"Runeseer removed {count} stale failure {noun}.")
            else:
                if action == "kept newer":
                    print("Runeseer kept the newer failure notice.")
                else:
                    print(f"Runeseer {action} the failure notice.")
            return 0
        if arguments.command == "reconcile-owner-escalation":
            body = (
                read_utf8(arguments.body).rstrip("\n")
                if arguments.body is not None
                else None
            )
            action = reconcile_owner_escalation(
                body,
                arguments.repo,
                arguments.pr,
                arguments.author,
                arguments.head,
                arguments.base,
                arguments.review_round,
                arguments.run_id,
                arguments.summary_comment_id,
            )
            if action.startswith("removed "):
                count = int(action.split()[1])
                noun = "escalation" if count == 1 else "escalations"
                print(f"Runeseer removed {count} stale owner {noun}.")
            elif action == "kept newer":
                print("Runeseer kept the newer owner escalation.")
            else:
                print(f"Runeseer {action} the owner escalation.")
            return 0
        if arguments.command == "newer-summary":
            newer = published_summary_is_newer(
                arguments.repo,
                arguments.pr,
                arguments.author,
                arguments.head,
                arguments.base,
                arguments.review_round,
                arguments.run_id,
                arguments.summary_comment_id,
            )
            print("true" if newer else "false")
            return 0
        if arguments.command == "read-ledger":
            stored = read_stored_ledger(
                load_json(arguments.verdict), arguments.sha, arguments.round
            )
            write_output(json.dumps(stored, indent=2), arguments.output)
            return 0
        if arguments.command == "validate-verdict":
            validate_verdict(
                load_json(arguments.verdict),
                arguments.sha,
                arguments.round,
                load_lane_comments(arguments.lane_comments),
                load_lane_comments(arguments.runeseer_comments),
                load_previous_findings(arguments.previous_verdict),
                load_runeseer_records(arguments.runeseer_findings),
                load_ledger(arguments.ledger),
            )
            return 0
        if arguments.command == "build-ledger":
            overrides: dict[str, str] = {}
            for entry in arguments.lane_status:
                lane_id, separator, status = entry.partition("=")
                if not separator:
                    raise SummaryError(f"The lane status must read LANE=STATUS: {entry}")
                overrides[lane_id] = status
            labels = load_json(arguments.labels)
            if not isinstance(labels, list) or not all(
                isinstance(label, str) for label in labels
            ):
                raise SummaryError("The labels file must contain an array of names.")
            ledger = build_ledger(
                pull_number=arguments.pr,
                head=arguments.head,
                base=arguments.base,
                lanes=load_lane_table(load_json(arguments.lane_table)),
                thread_nodes=load_json(arguments.threads),
                check_runs=load_json(arguments.check_runs),
                labels=labels,
                body=read_utf8(arguments.body),
                previous=load_ledger(arguments.previous_ledger),
                overrides=overrides,
                paid_rounds=arguments.paid_rounds,
            )
            write_output(json.dumps(ledger, indent=2), arguments.output)
            return 0
        if arguments.command == "triage":
            ledger = validate_ledger(load_json(arguments.ledger))
            files = [
                line for line in read_utf8(arguments.files).splitlines() if line
            ]
            reason = triage(
                ledger,
                files,
                comments=arguments.comments,
                forced=arguments.forced,
                volume_limit=arguments.volume_limit,
                instruction_pattern=arguments.instruction_pattern,
            )
            ledger["coverage"] = coverage_state(reason)
            if reason is not None and "runeseer" in ledger["lanes"]:
                ledger["lanes"]["runeseer"] = "skipped"
            if arguments.output is not None:
                write_output(json.dumps(ledger, indent=2), arguments.output)
            print(ledger["coverage"])
            return 0
        if arguments.command == "apply-verdict":
            ledger = apply_verdict_to_ledger(
                load_json(arguments.ledger), load_json(arguments.verdict)
            )
            write_output(json.dumps(ledger, indent=2), arguments.output)
            return 0
        if arguments.command == "ledger-stale":
            stale = ledger_is_stale(
                load_json(arguments.ledger), load_json(arguments.threads)
            )
            print("true" if stale else "false")
            return 0
        if arguments.command == "work-item":
            print(work_item_from_body(read_utf8(arguments.body), arguments.pr))
            return 0
        if arguments.command == "standdown-notice":
            print(standdown_notice(load_json(arguments.ledger)))
            return 0
        if arguments.command == "ledger-line":
            print(ledger_line(arguments.ledger.read_bytes(), arguments.artifact_id))
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
                arguments.session_stats,
                arguments.runeseer_findings,
                arguments.ledger,
            )
            write_output(body, arguments.output)
            return 0
        if arguments.command != "publish":
            raise SummaryError(f"The command is not implemented: {arguments.command}")

        body = read_utf8(arguments.body).rstrip("\n")
        try:
            action, summary_comment_id = publish_summary(
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
        print(f"summary-comment-id={summary_comment_id}")
        return 0
    except (OSError, SummaryError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
