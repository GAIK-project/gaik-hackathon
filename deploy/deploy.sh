#!/usr/bin/env bash
# Build in the cluster from git HEAD and roll out. Run from Git Bash: deploy/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
NS=gaik
APP=gaik-hackathon
[[ "$(oc project -q)" == "$NS" ]] || { echo "wrong project: $(oc project -q)"; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || echo "note: uncommitted changes are not in the image"
SRC_DIR=$(mktemp -d); trap 'rm -rf "$SRC_DIR"' EXIT
git archive HEAD | tar -x -C "$SRC_DIR"
oc apply -f deploy/manifests/00-build.yaml
oc start-build "$APP" -n "$NS" --from-dir="$SRC_DIR" --follow --wait
oc apply -f deploy/manifests/10-app.yaml
oc rollout restart "deploy/$APP" -n "$NS"
oc rollout status "deploy/$APP" -n "$NS" --timeout=420s
curl -fsS "https://$APP.2.rahtiapp.fi/api/health" && echo
