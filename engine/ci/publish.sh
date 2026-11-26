#!/usr/bin/env bash
# Called by .github/workflows/update-feed.yml after `python -m newsfeed`.
# 1) Saves engine/data/state.db to the `state` branch (single force-pushed commit).
# 2) Copies engine/output/feed.xml to /news_feed.xml on main and pushes if changed.
# 3) Once per Kiev calendar day, squashes main's feed commits so the repo stays small.
# Must be run from the repo root on a checkout of main with full history.
set -euo pipefail

KIEV_TZ="Europe/Kyiv"
kiev_now() { TZ="$KIEV_TZ" date '+%Y-%m-%d %H:%M:%S'; }
kiev_date() { TZ="$KIEV_TZ" date +%F; }

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# ---- 1. state branch (must succeed before the feed is published) ----
DB="engine/data/state.db"
[[ -s "$DB" ]] || { echo "ERROR: $DB missing or empty"; exit 1; }
new_blob=$(git hash-object -w "$DB")
old_blob=$(git rev-parse -q --verify "origin/state:state.db" 2>/dev/null || true)
if [[ "$new_blob" != "$old_blob" ]]; then
  tree=$(printf '100644 blob %s\tstate.db\n' "$new_blob" | git mktree)
  commit=$(git commit-tree "$tree" -m "state.db $(kiev_now) Europe/Kiev")
  git push --force origin "$commit:refs/heads/state"
  echo "state branch updated ($(stat -c %s "$DB") bytes)"
else
  echo "state.db unchanged"
fi

# ---- 2. feed on main ----
cp -f engine/output/feed.xml news_feed.xml
items=$(grep -o '<item[ >]' news_feed.xml | wc -l)
when="$(kiev_now) Europe/Kiev"

# Commits after the last one that touched the workflow files are feed updates
# made by this job; they are what the daily squash folds together.
base=$(git log -1 --format=%H -- .github/workflows)
today=$(kiev_date)
first_after_base=$(TZ="$KIEV_TZ" git log --reverse --format=%cd --date=format-local:%F "$base..HEAD" | head -1)
count_after_base=$(git rev-list --count "$base..HEAD")

changed=1
git diff --quiet -- news_feed.xml && changed=0
need_squash=0
if [[ "$count_after_base" -gt 1 && -n "$first_after_base" && "$first_after_base" != "$today" ]]; then
  need_squash=1
fi

if [[ "$changed" -eq 0 && "$need_squash" -eq 0 ]]; then
  echo "feed unchanged ($items items); nothing to push"
  exit 0
fi

if [[ "$need_squash" -eq 1 ]]; then
  git add news_feed.xml
  tree=$(git write-tree)
  msg="Squash feed history: ${items} items, ${when}"
  commit=$(git commit-tree "$tree" -p "$base" -m "$msg")
  git push --force-with-lease="main:$(git rev-parse HEAD)" origin "$commit:refs/heads/main"
  echo "published (daily squash onto ${base:0:7}, ${count_after_base} commits folded): $msg"
  exit 0
fi

git add news_feed.xml
git commit -q -m "Update feed: ${items} items, ${when}"
git push origin HEAD:main
echo "published: ${items} items"
