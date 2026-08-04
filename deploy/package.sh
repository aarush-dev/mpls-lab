#!/usr/bin/env bash
# package.sh — Bundle the whole lab (+ optional Claude context) into tarballs you copy
# to another machine. Read-only on your SOURCE TREE except that it refreshes
# airgap/images/ + manifest.txt via `docker save` (and `docker pull` for registry images,
# so it needs internet unless SKIP_IMAGE_SAVE=1). No other files are modified.
#
# The lab is NOT reproducible from git alone: topology/, airgap/images/, copilot/.env,
# WireGuard keys, datasets and runtime DBs are gitignored. So we tar the whole working dir.
#
# Output (in the repo's parent dir, default /root):
#   noc-lab-bundle-<ts>.tar.gz     — all of /root/LAB (extract to /root)
#   claude-context-<ts>.tar.gz     — ~/.claude chats+skills+plugins+settings + ~/.claude.json
#
# Usage:
#   ./deploy/package.sh                 # lab + claude context (default: "everything")
#   ./deploy/package.sh --lean          # drop regenerable giants (synthetic/output, dataapi/datasets)
#   ./deploy/package.sh --no-claude     # lab bundle only
#   SKIP_IMAGE_SAVE=1 ./deploy/package.sh   # reuse existing airgap/images (skip docker save)
set -euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
NAME="$(basename "$REPO")"                 # MUST be "LAB"
# Target hard-codes /root/LAB. A checkout named anything else produces a tarball that
# extracts to /root/<other> and restore.sh then hard-fails. Catch it here, at pack time.
[[ "$NAME" == LAB ]] || { echo "ERROR: source dir is '$NAME', must be 'LAB'"; exit 1; }
PARENT="$(dirname "$REPO")"                # normally /root
TS="$(date +%Y%m%d-%H%M%S)"

LEAN=0; CLAUDE=1
for a in "$@"; do case "$a" in
  --lean) LEAN=1 ;; --no-claude) CLAUDE=0 ;;
  *) echo "unknown flag: $a"; exit 1 ;;
esac; done

# 1. Refresh the offline docker-image bundle (3 local builds + registry incl kafka).
#    Only source of frr-node/noc-controller/noc-trafficgen — no registry pulls them.
if [[ "${SKIP_IMAGE_SAVE:-0}" != 1 ]]; then
  echo "== saving docker images =="; "$REPO/airgap/pull-and-save.sh"
else
  echo "== SKIP_IMAGE_SAVE=1 — reusing existing airgap/images/ =="
fi

# 2. Lab bundle: whole dir, tracked + untracked. Exclude only cruft (+ giants in --lean).
LAB_OUT="$PARENT/noc-lab-bundle-$TS.tar.gz"
EX=( --exclude="$NAME/**/__pycache__" --exclude="$NAME/__pycache__"
     --exclude="*.pyc" --exclude="*.log"
     --exclude="$NAME/**/node_modules" --exclude="$NAME/node_modules"
     --exclude="*:Zone.Identifier" )
[[ $LEAN == 1 ]] && EX+=( --exclude="$NAME/synthetic/output" --exclude="$NAME/dataapi/datasets" ) \
                 && echo "== --lean: dropping synthetic/output + dataapi/datasets (regenerable) =="
echo "== taring lab → $LAB_OUT =="
tar "${EX[@]}" -C "$PARENT" -czf "$LAB_OUT" "$NAME"

# 3. Claude context: chats (projects/), skills+plugins, settings, prompt history, creds.
#    Exclude ephemeral caches/logs/running-session state — portable state only.
if [[ $CLAUDE == 1 && -d "$HOME/.claude" ]]; then
  CL_OUT="$PARENT/claude-context-$TS.tar.gz"
  CLEX=( --exclude=".claude/session-env" --exclude=".claude/shell-snapshots"
         --exclude=".claude/paste-cache" --exclude=".claude/cache"
         --exclude=".claude/file-history" --exclude=".claude/downloads"
         --exclude=".claude/chrome"      --exclude=".claude/sessions"
         --exclude=".claude/daemon"      --exclude=".claude/telemetry"
         --exclude=".claude/jobs"        --exclude=".claude/tasks"
         --exclude=".claude/*.log"       --exclude=".claude/projects/-tmp-*" )
  echo "== taring claude context → $CL_OUT =="
  # ~/.claude.json holds MCP servers + project state; include it. Both extract to $HOME.
  tar "${CLEX[@]}" -C "$HOME" -czf "$CL_OUT" .claude .claude.json
fi

echo
echo "== done =="
du -sh "$LAB_OUT" ${CL_OUT:+"$CL_OUT"} 2>/dev/null | sed 's/^/  /'
echo "SECRETS INSIDE: copilot/.env (nvapi keys), generator/.wg-keys.json, and (claude bundle)"
echo "  .credentials.json + .claude.json oauth tokens. Move over a private channel; scrub after."
echo "TIP: stop noc-copilot first (systemctl stop noc-copilot) so ledger.db sqlite isn't copied mid-write."
echo "resetup on target: see deploy/README.md"
