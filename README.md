# Rune Seer

> Rune review and observation bot.

Seer is the review machinery behind two GitHub Apps: **runeseer**, the reviewing identity, and **runewright**, the acting one. Both must be installed on the org and configured as Apps — credentials as org secrets, permissions per identity — before any lane runs; [INSTALL.md](INSTALL.md) carries the wiring. The workflows in this repository are the hands of both: a lane minting a runewright token acts (labels, comments, patches), and a lane minting a runeseer token reviews (verdicts, the earned approval). The identity that can write content holds no approval role, and the identity that approves cannot write content.

## Lanes

| Workflow             | Summoned by                     | What it does                                                              |
| -------------------- | ------------------------------- | ------------------------------------------------------------------------- |
| `review-cascade`     | bare `review` label             | Runs Macroscope correctness, then sends its findings to Runeseer. |
|                      |                                 | Missing reviews, provider failures, and moved heads preserve the request. |
| `review-cursor`      | `review:cursor` label           | Dispatches optional Bugbot. A failed dispatch preserves the request. |
| `review-correctness` | `review:runeseer` label         | Adjudicates the free lanes' findings, reviews the diff, records a          |
|                      |                                 | machine-readable verdict, earns the approval on an explicit clean          |
|                      |                                 | verdict for the live head, and consumes the review labels.                 |
| `autofix-suggest`    | `review:autofix` label          | Untrusted half: runs the fixers with no secrets, uploads a patch.          |
| `autofix-comment`    | completed `autofix-suggest`     | Trusted half: binds the artifact to its run, posts the suggestion.         |
| `congrats`           | push to `main`                  | Greets a contributor's first merged pull request.                          |
| `issue-dedup`        | issue opened                    | Flags probable duplicates, referencing only gathered candidates.           |
| `thread-resolver`    | push to a pull request          | Resolves threads named by `Resolves-Thread:` trailers, same PR only.       |
| `dashboard`          | schedule, runs here             | Sweeps the org for pull requests waiting only on the owner and keeps       |
|                      |                                 | the "Awaiting owner review" issue current.                                 |

Bugbot, Cursor Security Agent, and CodeRabbit are optional, independent reviews.
Their absence or failure does not block the default cascade.
A Bugbot dispatch records a request, not a completed review.
A Cursor Security Agent check never supplies Bugbot completion evidence.

The cascade requires Macroscope core correctness evidence for the current head.
A `success` result means clean. A `neutral` result sends findings to Runeseer for adjudication.
Skipped checks, approvability, and custom agents supply no core correctness evidence.
Existing stage labels are informational. Each round checks the current head again.
Runeseer remains the adjudicating lane.

## Caller contract

A repository subscribes through workflow files: GitHub triggers only what exists in a repo's own `.github/workflows/`, so each repo carries a stub per lane that delegates with `uses: runedeck/seer/.github/workflows/<lane>.yaml@main`. Callers own the triggers, the concurrency, and the permissions; bodies own the logic and declare the secrets they need explicitly. First-party references ride `@main`: the trust boundary is push access to this repository, and its rulesets and history answer for every lane.

Pass the trusted `MACROSCOPE_CORRECTNESS_CHECK` repository variable to the `macroscope_correctness_check` input.
The value must identify the observed core correctness check, not an approvability or custom-agent check.
An empty value blocks the cascade with a configuration error. [INSTALL.md](INSTALL.md) describes the required provider verification.

The cascade caller grants `contents: read`, `checks: read`, `issues: write`, and `pull-requests: write`.
Workflow-token consumption emits no workflow event. App tokens dispatch downstream labels but never consume the entry request.
Only a new `review` label event cancels an active cascade. An `unlabeled` event does not cancel it.

## Canon

The ceremony specification lives in [skeleton](https://github.com/runedeck/skeleton) under `docs/specs/review-ceremony/`, and the lane dashboard configuration in its `docs/guides/review-lanes-configuration.md`. This repository is the machinery, not the canon.

## Roadmap

- Callers pin signed tags (`@v1`) instead of `@main` once the first seer release is cut, so every repo names the exact machinery it trusts
- A shared definitions file (lane names, thresholds, prompts, dashboard strings) read by the workflows, replacing per-file constants
- The dashboard grows from one queue issue into the org's review ledger: per-repo funnel state, round costs from the correctness metrics, and lane latency
