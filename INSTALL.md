# Install seer's lanes into the org

> Wire the org so seer's reusable lanes can run from any repository's thin callers.

Seer needs no build and no deploy of its own; installing it means giving the org the secrets, labels, and dashboard state the lanes assume, then letting callers reference `runedeck/seer@main`.

OBJECTIVE: every repository with lane callers can summon a full funnel round.

DONE WHEN: applying the bare `review` label on a ready pull request in a caller repository runs `cascade / walk` green through all three stages, and an unlabeled push summons nothing.

TODO:

- [ ] Org secrets exist with All-repositories visibility: `RUNESEER_APP_ID`, `RUNESEER_APP_KEY`, `RUNEWRIGHT_APP_ID`, `RUNEWRIGHT_APP_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`
- [ ] Both apps are installed on the org: runeseer (contents read, pull requests and issues write), runewright (contents and workflows write, pull requests and issues write)
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

EXECUTE NOW: work the TODO list top to bottom, then run the DONE WHEN check on a scratch pull request.
