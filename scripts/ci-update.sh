#!/usr/bin/env bash
set -euo pipefail
stage=${1:?Use prepare or publish}
: "${DEFAULT_BRANCH:?Default branch is required}"

ConfigureGit() {
  git config --local user.name 'github-actions[bot]'
  git config --local user.email '41898282+github-actions[bot]@users.noreply.github.com'
}
if [[ "$stage" == prepare ]]; then
  # No changes, commits, pushes, or GitHub writes for unchanged/older releases.
  baselineRelease=$(cat nix/release.json)
  nix run .#update -- --write > /tmp/ednovas-update.json
  changed=$(jq -er '.changed | if type == "boolean" then . else error("invalid changed") end' /tmp/ednovas-update.json) || {
    if [[ $(jq -r '.changed' /tmp/ednovas-update.json) != false ]]; then exit 1; fi
    changed=false
  }
  if [[ "$changed" == false ]]; then
    echo 'changed=false' >> "${GITHUB_OUTPUT:-/dev/stdout}"
    exit 0
  fi
  releaseVersion=$(jq -er '.latestVersion' /tmp/ednovas-update.json)
  ConfigureGit
  git add -- nix/release.json
  git commit -m "Update EdNovas Cloud to $releaseVersion"
  python3 scripts/candidate.py prepare --baseline-release "$baselineRelease"
  echo 'changed=true' >> "${GITHUB_OUTPUT:-/dev/stdout}"
elif [[ "$stage" == publish ]]; then
  : "${GH_TOKEN:?Publication token is required}"
  : "${GH_REPO:?GitHub repository is required}"
  : "${RUN_URL:?Workflow URL is required}"
  : "${VALIDATION_NEEDS:?Validation job results are required}"
  ConfigureGit
  python3 scripts/candidate.py gate --needs "$VALIDATION_NEEDS"
  # Git's in-memory HTTP configuration supplies authentication. The
  # token is never persisted in the archived checkout or command arguments.
  export GIT_CONFIG_COUNT=1
  export GIT_CONFIG_KEY_0=http.extraHeader
  GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)"
  export GIT_CONFIG_VALUE_0
  python3 scripts/candidate.py acceptance
  updateCommit=$(jq -er '.identity.commit' .candidate/manifest.json)
  parentCommit=$(jq -er '.identity.parent' .candidate/manifest.json)
  releaseVersion=$(jq -er '.release.version' .candidate/manifest.json)
  git fetch --prune origin '+refs/heads/*:refs/remotes/origin/*'
  if [[ $(git rev-parse "refs/remotes/origin/$DEFAULT_BRANCH") != "$parentCommit" ]]; then
    echo 'Default branch advanced; prepare and validate a new candidate before publication.' >&2
    exit 1
  fi
  python3 scripts/candidate.py verify
  remoteUpdateCommit=$(git rev-parse --verify refs/remotes/origin/ednovas-update 2>/dev/null || true)
  # Refresh sibling candidate commits, but never overwrite a concurrent push.
  git push --force-with-lease="refs/heads/ednovas-update:$remoteUpdateCommit" origin "$updateCommit:refs/heads/ednovas-update"
  # Confirm the actual remote head and base before reporting success or merging.
  remoteCommit=$(gh api "repos/$GH_REPO/git/ref/heads/ednovas-update" --jq '.object.sha')
  remoteParent=$(gh api "repos/$GH_REPO/commits/$remoteCommit" --jq '.parents | if length == 1 then .[0].sha else "invalid" end')
  [[ "$remoteCommit" == "$updateCommit" && "$remoteParent" == "$parentCommit" ]]
  [[ $(gh api "repos/$GH_REPO/git/ref/heads/$DEFAULT_BRANCH" --jq '.object.sha') == "$parentCommit" ]]
  jq -n --arg targetUrl "$RUN_URL" '{state:"success",context:"ednovas-update/build",target_url:$targetUrl,description:"Builds, modules, X11, niri, and both bundled cores passed"}' > .candidate/status.json
  gh api --method POST "repos/$GH_REPO/statuses/$updateCommit" --input .candidate/status.json
  cat > .candidate/body.md <<BODY
Update the pinned EdNovas Cloud release to $releaseVersion.

Three independent acceptance runs and all ten deliberate fault checks passed for this candidate. All required suites passed on this exact commit: production builds, unit/module checks and workflow linting; X11 and niri login rendering, interaction, authenticated privileged launch and repeated hide/restore/quit; both bundled Mihomo cores over local HTTP, SOCKS and captured TUN traffic. [Validation and diagnostic artifacts]($RUN_URL).

Fresh unauthenticated profiles and software rendering only. Server-configuration dialog interaction, subscription loading, authenticated GUI connections, GPU behavior and authenticated profile migration remain manual checks. Bundled-core coverage does not prove the GUI generates or applies a subscription configuration.
BODY
  prNumber=$(gh pr list --repo "$GH_REPO" --state open --head ednovas-update --base "$DEFAULT_BRANCH" --json number --jq '.[0].number // empty')
  if [[ -z "$prNumber" ]]; then
    gh pr create --repo "$GH_REPO" --head ednovas-update --base "$DEFAULT_BRANCH" --title "Update EdNovas Cloud to $releaseVersion" --body-file .candidate/body.md
    prNumber=$(gh pr list --repo "$GH_REPO" --state open --head ednovas-update --base "$DEFAULT_BRANCH" --json number --jq '.[0].number')
  else
    gh pr edit "$prNumber" --repo "$GH_REPO" --title "Update EdNovas Cloud to $releaseVersion" --body-file .candidate/body.md
  fi
  gh pr merge "$prNumber" --repo "$GH_REPO" --auto --squash --match-head-commit "$updateCommit"
else
  exit 2
fi
