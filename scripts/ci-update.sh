#!/usr/bin/env bash
set -euo pipefail
stage=${1:?Use prepare or publish}
: "${DEFAULT_BRANCH:?Default branch is required}"

IdentityCommit() { jj log --no-pager -r "$1" -G -T commit_id; }
ConfigureJj() {
  if [[ ! -d .jj ]]; then jj git init --colocate; fi
  jj config set --repo user.name '"github-actions[bot]"'
  jj config set --repo user.email '"41898282+github-actions[bot]@users.noreply.github.com"'
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
  ConfigureJj
  jj describe -m "Update EdNovas Cloud to $releaseVersion"
  python3 scripts/candidate.py prepare --baseline-release "$baselineRelease"
  echo 'changed=true' >> "${GITHUB_OUTPUT:-/dev/stdout}"
elif [[ "$stage" == publish ]]; then
  : "${GH_TOKEN:?Publication token is required}"
  : "${GH_REPO:?GitHub repository is required}"
  : "${RUN_URL:?Workflow URL is required}"
  : "${VALIDATION_NEEDS:?Validation job results are required}"
  ConfigureJj
  python3 scripts/candidate.py gate --needs "$VALIDATION_NEEDS"
  # Jujutsu's Git subprocess honors Git's in-memory HTTP configuration. The
  # token is never persisted in the archived checkout or command arguments.
  export GIT_CONFIG_COUNT=1
  export GIT_CONFIG_KEY_0=http.extraHeader
  GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)"
  export GIT_CONFIG_VALUE_0
  jj config set --repo git.subprocess true
  python3 scripts/candidate.py acceptance
  updateCommit=$(jq -er '.identity.commit' .candidate/manifest.json)
  parentCommit=$(jq -er '.identity.parent' .candidate/manifest.json)
  releaseVersion=$(jq -er '.release.version' .candidate/manifest.json)
  jj git fetch --remote origin --branch "$DEFAULT_BRANCH" --branch ednovas-update
  if [[ $(IdentityCommit "$DEFAULT_BRANCH@origin") != "$parentCommit" ]]; then
    echo 'Default branch advanced; prepare and validate a new candidate before publication.' >&2
    exit 1
  fi
  python3 scripts/candidate.py verify
  if [[ -n $(jj bookmark list --no-pager --remote origin ednovas-update -T 'name') ]]; then
    jj bookmark track ednovas-update@origin
  fi
  # Squash merges and refreshed candidates produce sibling commits. Move the
  # topic bookmark explicitly; the native push still checks its remote lease.
  jj bookmark set --allow-backwards ednovas-update -r "$updateCommit"
  jj git push --remote origin --bookmark ednovas-update
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
