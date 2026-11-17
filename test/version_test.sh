#!/usr/bin/env bash
# The image, its tag and the build sheet have to agree with the upstream commit
# they claim to carry. Each mismatch below is silent at build time:
#
#   - Bun older than upstream's packageManager can still run `bun install
#     --frozen-lockfile`, or refuse it, depending on what the lockfile uses.
#   - A CI tag that names the wrong upstream version publishes a misleading
#     image that the template then pins.
#   - template.json is the master for the published config; an image ref there
#     that CI never pushes is a deploy that fails at pull time.
#
# Needs network: it reads upstream's package.json at the pinned commit.
set -uo pipefail

cd "$(dirname "$0")/.."

fail=0
check() { # check <name> <expected> <actual>
	if [ "$2" = "$3" ]; then
		echo "ok    $1 ($3)"
	else
		echo "FAIL  $1: expected '$2', got '$3'"
		fail=1
	fi
}

ref=$(sed -n 's/^ARG CRM_REF=\([0-9a-f]\{40\}\)$/\1/p' Dockerfile)
[ -n "$ref" ] || { echo "FAIL  no CRM_REF in Dockerfile"; exit 1; }

pkg=$(curl -fsS "https://raw.githubusercontent.com/trycompai/crm/$ref/package.json") ||
	{ echo "FAIL  could not read upstream package.json at $ref"; exit 1; }

upstream_version=$(printf '%s' "$pkg" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')
upstream_bun=$(printf '%s' "$pkg" | python3 -c 'import json,sys; print(json.load(sys.stdin)["packageManager"].split("@")[1])')

image_bun=$(sed -n 's/^FROM oven\/bun:\([0-9.]*\)-debian$/\1/p' Dockerfile)
check "Dockerfile bun matches upstream packageManager" "$upstream_bun" "$image_bun"

ci_tag=$(sed -n 's/.*comp-ai-crm-railway:\([0-9][^ ]*\)$/\1/p' .github/workflows/build.yml)
check "CI tag carries upstream version" "$upstream_version" "${ci_tag%-r*}"

sheet_ref=$(python3 -c 'import json; print(json.load(open("template.json"))["image"]["ref"])')
check "template.json image is the CI tag" "ghcr.io/hmseeb/comp-ai-crm-railway:$ci_tag" "$sheet_ref"

sheet_commit=$(python3 -c 'import json; print(json.load(open("template.json"))["upstream"]["commit"])')
check "template.json upstream commit is CRM_REF" "$ref" "$sheet_commit"

[ "$fail" -eq 0 ]
