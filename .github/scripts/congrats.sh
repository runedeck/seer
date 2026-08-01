#!/usr/bin/env bash
set -euo pipefail

# Greet a contributor's first merged pull request in this repository.
# Counting walks the pulls list directly: the search index lags fresh
# merges, and a lagged count silences the greeting forever.

pr=$(gh api "repos/$REPO/commits/$MERGE_SHA/pulls" \
    --jq '[.[] | select(.merged_at != null)] | first')
[ -n "$pr" ] && [ "$pr" != "null" ] || { echo "no merged PR for this push"; exit 0; }

IFS=$'\t' read -r author author_type number \
    < <(jq -r '[.user.login, .user.type, .number] | @tsv' <<<"$pr")
[ "$author_type" = "Bot" ] && { echo "bot author, no greeting"; exit 0; }
[ "$author" = "N4M3Z" ] && { echo "owner, no greeting"; exit 0; }

merged=$(gh api --paginate "repos/$REPO/pulls?state=closed&per_page=100" \
    --jq "[.[] | select(.user.login == \"$author\" and .merged_at != null)] | length" \
    | awk '{ total += $1 } END { print total + 0 }')
if [ "$merged" -le 1 ]; then
    gh api -X POST "repos/$REPO/issues/$number/comments" \
        -f body="Your first merged contribution here — welcome, @$author, and thank you. Every rune in this deck was reviewed the same way yours just was; may it be the first of many." >/dev/null
    echo "greeted $author on #$number"
else
    echo "$author has $merged merged PRs here, no greeting"
fi
