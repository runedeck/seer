# Rune Seer

> Rune review and observation bot.

Seer is the review machinery behind two GitHub Apps: **runeseer**, the reviewing identity, and **runewright**, the acting one. Both must be installed on the org and configured as Apps (credentials as org secrets, permissions per identity) before any lane runs. [INSTALL.md](INSTALL.md) carries the setup. The workflows in this repository are the hands of both: a lane minting a runewright token acts (labels, comments, patches), and a lane minting a runeseer token reviews (verdicts, the earned approval). The identity that can write content holds no approval role, and the identity that approves cannot write content.

## Lanes

| Workflow             | Summoned by                     | What it does                                                              |
| -------------------- | ------------------------------- | ------------------------------------------------------------------------- |
| `review-cascade`     | bare `review` label             | Runs Macroscope correctness, then sends its findings to Runeseer. |
|                      |                                 | Missing reviews, provider failures, and moved heads preserve the request. |
| `review-cursor`      | `review:cursor` label           | Dispatches optional Bugbot. A failed dispatch preserves the request. |
| `review-correctness` | ready, push to a ready pull     | The controller. Builds the ledger from the API by lane login, triages    |
|                      | request, reopen, body edit      | the head, adjudicates the free lanes' findings, records a verdict bound  |
|                      | (ledger only), `review:runeseer` | to the head and ledger generation, resolves the threads the verdict      |
|                      | label                           | disposed as `fixed`, earns the approval on a clean verdict with every    |
|                      |                                 | thread disposed, and consumes the review labels.                         |
| `autofix-suggest`    | `review:autofix` label          | Untrusted half: runs the fixers with no secrets, uploads a patch.          |
| `autofix-comment`    | completed `autofix-suggest`     | Trusted half: binds the artifact to its run, posts the suggestion.         |
| `congrats`           | push to `main`                  | Greets a contributor's first merged pull request.                          |
| `issue-dedup`        | issue opened                    | Flags probable duplicates, referencing only gathered candidates.           |
| `thread-resolver`    | push to a pull request          | Retired: resolution lives in the correctness round. Callers can drop it. |
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

A repository subscribes through workflow files: GitHub triggers only what exists in a repo's own `.github/workflows/`, so each repo carries a stub per lane that delegates with `uses: runedeck/seer/.github/workflows/<lane>.yaml@main`. Callers own the triggers, the concurrency, and the permissions. Bodies own the logic and declare the secrets they need explicitly. First-party references ride `@main`: the trust boundary is push access to this repository, and its rulesets and history answer for every lane.

Pass the trusted `MACROSCOPE_CORRECTNESS_CHECK` repository variable to the `macroscope_correctness_check` input.
The value must identify the observed core correctness check, not an approvability or custom-agent check.
An empty value blocks the cascade with a configuration error. [INSTALL.md](INSTALL.md) describes the required provider verification.

The cascade caller grants `contents: read`, `checks: read`, `issues: write`, and `pull-requests: write`.
Workflow-token consumption emits no workflow event. App tokens dispatch downstream labels but never consume the entry request.
Only a new `review` label event cancels an active cascade. An `unlabeled` event does not cancel it.

## Known gaps

- The open-seal is verified by `owner-seal`, which lives in the skeleton track and does not exist yet. Until it does, the controller starts the funnel on any ready event of a same-repository pull request, and a contributor with push access can ready a draft by hand and spend a paid round. The nonce line `Open-Seal-Nonce:` is not checked here.
- A late review thread runs the mirror, which reports the approval stale and dismisses it. The ledger generation itself moves on the next controller run. A late lane check run (a free lane that completes after the controller ran) moves the generation only on the next pull request event. The `check_run` event carries no pull request payload, so the entry workflow cannot route it to the controller.
- The lane table accepts an optional `check_slug` per lane for the app slug that reports its check runs. The Codex and Cursor slugs are not verified against a live head and default to the login slug.
- The ledger has two carriers. The artifact holds the full record and is trusted by the run that uploaded it. The check run named `ledger` on `reviewed_sha`, written under the runeseer app, carries one line `ledger: {artifact_id, digest, generation, pull_request, reviewed_sha}`. `owner-seal` and `rune sign` read that line, and a reader who needs the threads fetches the artifact and proves it by the digest. The runeseer app needs `checks: write` for this, an installation change the owner accepts once.
- A round-start marker is a Runeseer comment. A user with write access can delete it, and the budget then undercounts by that round.

## Canon

The ceremony specification lives in [skeleton](https://github.com/runedeck/skeleton) under `docs/specs/review-ceremony/`, and the lane dashboard configuration in its `docs/guides/review-lanes-configuration.md`. This repository is the machinery, not the canon.

## Roadmap

- Callers pin signed tags (`@v1`) instead of `@main` once the first seer release is cut, so every repo names the exact machinery it trusts
- A shared definitions file (lane names, thresholds, prompts, dashboard strings) read by the workflows, replacing per-file constants
- The dashboard grows from one queue issue into the org's review ledger: per-repo funnel state, round costs from the correctness metrics, and lane latency
