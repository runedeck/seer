# Rune Seer

> Rune review and observation bot.

Seer is the review machinery behind two GitHub Apps: **runeseer**, the reviewing identity, and **runewright**, the acting one. Both must be installed on the org and configured as Apps — credentials as org secrets, permissions per identity — before any lane runs; [INSTALL.md](INSTALL.md) carries the wiring. The workflows in this repository are the hands of both: a lane minting a runewright token acts (labels, comments, patches), and a lane minting a runeseer token reviews (verdicts, the earned approval). The identity that can write content holds no approval role, and the identity that approves cannot write content.

## Lanes

| Workflow             | Summoned by                     | What it does                                                              |
| -------------------- | ------------------------------- | ------------------------------------------------------------------------- |
| `review-cascade`     | bare `review` label             | Walks the funnel: `bugbot run`, cursor settles clean, `review:macroscope`, |
|                      |                                 | settles clean, `review:runeseer`. Stops visibly on findings, a moved       |
|                      |                                 | head, or a fork after the free lanes.                                      |
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

## Caller contract

A repository subscribes through workflow files: GitHub triggers only what exists in a repo's own `.github/workflows/`, so each repo carries a stub per lane that delegates with `uses: runedeck/seer/.github/workflows/<lane>.yaml@main`. Callers own the triggers, the concurrency, and the permissions; bodies own the logic and declare the secrets they need explicitly. First-party references ride `@main`: the trust boundary is push access to this repository, and its rulesets and history answer for every lane.

## Canon

The ceremony specification lives in [skeleton](https://github.com/runedeck/skeleton) under `docs/specs/review-ceremony/`, and the lane dashboard configuration in its `docs/guides/review-lanes-configuration.md`. This repository is the machinery, not the canon.

## Roadmap

- Callers pin signed tags (`@v1`) instead of `@main` once the first seer release is cut, so every repo names the exact machinery it trusts
- A shared definitions file (lane names, thresholds, prompts, dashboard strings) read by the workflows, replacing per-file constants
- The dashboard grows from one queue issue into the org's review ledger: per-repo funnel state, round costs from the correctness metrics, and lane latency
