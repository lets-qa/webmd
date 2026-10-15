#!/usr/bin/env bash
# Usage: ci_already_passed.sh <commit-sha>
#
# Exits 0 (printing the earlier run) if a full CI run already passed on this
# exact code; exits 1 if not, so the caller must test. Any error also exits
# non-zero, which means "test".
#
# "This exact code" means the same git tree: byte-identical files and paths,
# tests and workflows included. Different commits (a merge, a squash, an empty
# commit) can share a tree, so we match on the tree.
#
# Evidence comes from markers, not run metadata. When every CI job passes, the
# ci-passed job uploads an artifact named tested-tree-<tree> for the tree it
# *actually checked out and tested*. That distinction matters for PRs: a
# pull_request run tests a temporary merge with the target branch, not the
# PR's head commit, so the head commit's tree proves nothing. Markers from fork
# PRs are ignored, because a fork controls its own copy of the workflow and
# could upload a marker without testing anything.
set -euo pipefail

sha=$1
repo=${GITHUB_REPOSITORY:?}
repo_id=${GITHUB_REPOSITORY_ID:-$(gh api "repos/$repo" --jq .id)}
tree=$(git rev-parse "$sha^{tree}")
echo "Looking for a CI pass on code tree $tree"

found=$(gh api "repos/$repo/actions/artifacts?name=tested-tree-$tree&per_page=100" \
  --jq '[.artifacts[]
         | select(.expired | not)
         | select(.workflow_run.head_repository_id == '"$repo_id"')
         | .workflow_run.id][0] // empty')

if [ -n "$found" ]; then
  echo "Already passed: https://github.com/$repo/actions/runs/$found"
  echo "run_id=$found" >> "${GITHUB_OUTPUT:-/dev/null}"
  exit 0
fi
echo "No CI pass recorded for this exact code"
exit 1
