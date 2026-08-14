# Install seer's lanes into the org

> Wire the org so seer's reusable lanes can run from any repository's thin callers.

Seer needs no build and no deploy of its own; installing it means giving the org the secrets, labels, and dashboard state the lanes assume, then letting callers reference `runedeck/seer@main`.

OBJECTIVE: every repository with lane callers can summon a full funnel round.

DONE WHEN: applying the bare `review` label on a ready pull request in a caller repository runs the cascade check green through all three stages, and an unlabeled push summons nothing.

TODO:

- [ ] Org secrets exist with All-repositories visibility: `RUNESEER_APP_ID`, `RUNESEER_APP_KEY`, `RUNEWRIGHT_APP_ID`, `RUNEWRIGHT_APP_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`
- [ ] Both apps are installed on the org: runeseer (contents read AND write, pull requests and issues write), runewright (contents and workflows write, pull requests and issues write)
- [ ] External reviewers configured per the skeleton's `docs/guides/review-lanes-configuration.md`: cursor mention-only with incremental review, macroscope label-only with drafts and auto-merge off
- [ ] The target repository carries the caller stubs (scaffolded from the skeleton's `templates/base`)
- [ ] Ceremony labels exist in the target repository (the first pr-lint run provisions them)

Steps:

```sh
gh secret set RUNESEER_APP_ID --org runedeck --visibility all --body "<id>"
gh secret set RUNESEER_APP_KEY --org runedeck --visibility all < runeseer.pem
gh secret set RUNEWRIGHT_APP_ID --org runedeck --visibility all --body "<id>"
gh secret set RUNEWRIGHT_APP_KEY --org runedeck --visibility all < runewright.pem
gh secret set CLAUDE_CODE_OAUTH_TOKEN --org runedeck --visibility all --body "<token from: claude setup-token>"
```

Dashboard state cannot be scripted; follow the guide named above and verify with its procedure: a draft pull request pushed twice summons nothing, and one `review` label runs the funnel in order.

## Requirements and permissions

- Required approvals count only write-access reviewers: an app holding `contents: read` submits APPROVED reviews that satisfy no ruleset. runeseer therefore carries `contents: read and write`, and the write/approve separation is enforced by its workflow's tool allowlist rather than by the permission bit.
- App permission changes take two acceptances: editing the registration (`organizations/<org>/settings/apps/<app>/permissions`) does nothing until the installation accepts the new scope (`organizations/<org>/settings/installations/<id>`). The installations API shows what is actually live.
- Nothing is retroactive: reviews and tokens carry the scope in force when they were minted. After a permission change, mint a fresh approval with a new round.

## Operating constraints

- `claude-code-action` refuses `track_progress` on labeled events, and labels are the standard review request: the flag is gated with `github.event.action != 'labeled'` so progress tracking runs everywhere the action supports it.
- The hosted runner's `gh` predates `--slurp`: paginate with per-page `--jq` arrays merged through `jq -s 'add'`.
- Round end consumes the review labels, and a failed round consumes them too. To re-summon: convert the pull request to draft, apply the label (the draft guard skips the run, so the label survives), then mark ready, which fires the review under an action every input accepts.
- A correctness round without a valid verdict posts one exact-head Runeseer pause notice. `issue:rune` remains the authoritative blocker. The notice links the failed workflow and never copies provider output. Inspect Actions logs for the cause. A successful current-head round removes the blocker.
- Never push after a clean verdict: stale-review dismissal removes the earned approval on any push, so documentation follow-ups ride the next pull request.

EXECUTE NOW: work the TODO list top to bottom, then run the DONE WHEN check on a scratch pull request.
