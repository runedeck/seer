# Install seer's lanes into the org

> Wire the org so seer's reusable lanes can run from any repository's thin callers.

Seer needs no build and no deploy of its own; installing it means giving the org the secrets, labels, and dashboard state the lanes assume, then letting callers reference `runedeck/seer@main`.

OBJECTIVE: every repository with lane callers can summon a full funnel round.

DONE WHEN: applying the bare `review` label on a ready pull request in a caller repository runs `cascade / walk` green through all three stages, and an unlabeled push summons nothing.

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

## Wiring truths, each learned from a live failure

- **Required approvals count only write-access reviewers.** An app holding `contents: read` submits APPROVED reviews that satisfy no ruleset; runeseer therefore carries `contents: read and write`, and the write/approve separation is enforced by its workflow's tool allowlist rather than by the permission bit.
- **App permission changes are two acceptances.** Editing the registration (`organizations/<org>/settings/apps/<app>/permissions`) does nothing until the installation accepts the new scope (`organizations/<org>/settings/installations/<id>`); the installations API shows what is actually live.
- **Nothing is retroactive.** Reviews and tokens carry the scope in force when they were minted; after a permission change, mint a fresh approval with a new round.
- **`claude-code-action` refuses `track_progress` on labeled events**, and labels are the standard summon: leave it off, the verdict summary comment is the round's record.
- **The hosted runner's `gh` predates `--slurp`**: paginate with per-page `--jq` arrays merged through `jq -s 'add'`.
- **The draft window re-arms a summon.** Round-end consumes the review labels, and a failed round consumes them too; to re-summon, convert the pull request to draft, apply the label (the lane's draft guard skips, so the label survives), then mark ready, which fires the lane under an action every input accepts.
- **Never push after a clean verdict.** Stale-review dismissal eats the earned approval on any push; documentation follow-ups ride the next pull request.

EXECUTE NOW: work the TODO list top to bottom, then run the DONE WHEN check on a scratch pull request.
