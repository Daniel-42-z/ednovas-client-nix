#!/usr/bin/env bash
set -euo pipefail

: "${GH_TOKEN:?GitHub Actions token is required to create and merge a PR}"
: "${GH_REPO:?GitHub repository is required}"
: "${DEFAULT_BRANCH:?Default branch is required}"
: "${RUN_URL:?Workflow URL is required}"

workPath=$(mktemp -d)
trap 'rm -rf "$workPath"' EXIT

nix run .#update -- --write > "$workPath/update.json"
updateChanged=$(jq -r '.changed' "$workPath/update.json")
if [[ "$updateChanged" != true ]]; then
  cat "$workPath/update.json"
  exit 0
fi
releaseVersion=$(jq -r '.latestVersion' "$workPath/update.json")

# This gate must succeed before any branch or PR is published.
nix flake check --print-build-logs

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git fetch origin "$DEFAULT_BRANCH"
updateBranch=ednovas-update
git ls-remote --heads origin "$updateBranch" > "$workPath/remote-ref"
expectedCommit=
if [[ -s "$workPath/remote-ref" ]]; then
  git fetch origin "refs/heads/$updateBranch:refs/remotes/origin/$updateBranch"
  expectedCommit=$(git rev-parse --verify "refs/remotes/origin/$updateBranch")
fi
git switch -C "$updateBranch"
git add nix/release.json
git commit -m "Update EdNovas Cloud to $releaseVersion"
updateCommit=$(git rev-parse HEAD)
git push "--force-with-lease=refs/heads/$updateBranch:$expectedCommit" \
  origin "HEAD:refs/heads/$updateBranch"

jq -n --arg targetUrl "$RUN_URL" '{
  state: "success", context: "ednovas-update/build", target_url: $targetUrl,
  description: "Both packages, module evaluation, and integration tests passed"
}' > "$workPath/status.json"
gh api --method POST "repos/$GH_REPO/statuses/$updateCommit" --input "$workPath/status.json"

cat > "$workPath/body.md" <<EOF
Update the pinned EdNovas Cloud release to $releaseVersion using the public download page.

Both proxy and TUN packages built successfully, and module and integration checks passed before this PR was created. [Validation run]($RUN_URL).

Live subscription and graphical TUN behavior are not tested by this workflow.
EOF

prNumber=$(gh pr list --repo "$GH_REPO" --state open --head "$updateBranch" \
  --base "$DEFAULT_BRANCH" --json number --jq '.[0].number // empty')
if [[ -z "$prNumber" ]]; then
  gh pr create --repo "$GH_REPO" --head "$updateBranch" --base "$DEFAULT_BRANCH" \
    --title "Update EdNovas Cloud to $releaseVersion" --body-file "$workPath/body.md"
  prNumber=$(gh pr list --repo "$GH_REPO" --state open --head "$updateBranch" \
    --base "$DEFAULT_BRANCH" --json number --jq '.[0].number')
else
  gh pr edit "$prNumber" --repo "$GH_REPO" \
    --title "Update EdNovas Cloud to $releaseVersion" --body-file "$workPath/body.md"
fi
gh pr merge "$prNumber" --repo "$GH_REPO" --auto --squash --match-head-commit "$updateCommit"
