#!/usr/bin/env bash
# restore.sh — set up the app on the target host after provision-debian.sh + extract.
# Loads docker images, installs python deps, installs+enables systemd units, restores
# Claude context if its bundle is present. Run AFTER:
#   sudo ./deploy/provision-debian.sh
#   tar xzf noc-lab-bundle-*.tar.gz -C /root       # → /root/LAB
#   tar xzf claude-context-*.tar.gz -C /root       # → ~/.claude (optional)   [handled below if not done]
set -euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$REPO"

# 0. Hard path check — the system hard-codes /root/LAB (.service files, sim-up cwd,
#    synthetic/discriminator.py, generator/generate.py). Refuse to run elsewhere.
[[ "$REPO" == /root/LAB ]] || { echo "ERROR: repo must be at /root/LAB (found $REPO)"; exit 1; }
command -v docker >/dev/null || { echo "ERROR: docker missing — run deploy/provision-debian.sh first"; exit 1; }

echo "== 1. load docker images (airgap/load-offline.sh) =="
./airgap/load-offline.sh

echo "== 2. python deps =="
# Debian 12 is PEP-668 externally-managed; the lab installs into the system interpreter
# because the run scripts call bare `python3 -m ...` / `uvicorn` off PATH.
# ponytail: --break-system-packages keeps those invocations working with zero script edits.
#           Ceiling: a /root/LAB/.venv + PATH shim if you'd rather not touch system site-packages.
while IFS= read -r req; do
  echo "  pip install -r $req"
  pip install -q --break-system-packages -r "$req"
done < <(find . \( -path ./refs -o -path ./.agents -o -path ./.claude -o -path '*/node_modules' \) -prune -o -name requirements.txt -print)

echo "== 3. systemd units =="
for u in noc-lab.service noc-copilot.service; do install -m644 "$u" "/etc/systemd/system/$u"; done
systemctl daemon-reload
systemctl enable noc-lab.service noc-copilot.service

echo "== 4. claude context =="
# If a sibling bundle wasn't already extracted, do it now (back up any existing ~/.claude first).
# A fresh `claude` install (provision installs the CLI) creates ~/.claude, so we can't gate on
# its existence. Gate on a .migrated marker instead — that only exists once WE restored a bundle.
CL=$(ls -t /root/claude-context-*.tar.gz 2>/dev/null | head -1 || true)
if [[ -n "$CL" && ! -f "$HOME/.claude/.migrated" ]]; then
  if [[ -d "$HOME/.claude" ]]; then
    bak=$([[ -f "$HOME/.claude.json" ]] && echo .claude.json || true)
    tar czf "$HOME/.claude.pre-restore.bak.tgz" -C "$HOME" .claude $bak
    echo "  backed up existing ~/.claude (+.json) → ~/.claude.pre-restore.bak.tgz"
  fi
  tar xzf "$CL" -C "$HOME" && touch "$HOME/.claude/.migrated" && echo "  restored $CL"
elif [[ -f "$HOME/.claude/.migrated" ]]; then
  echo "  claude context already migrated (skip)"
else
  echo "  no claude-context bundle found (skip)"
fi

echo
echo "== restored. bring the lab up: =="
echo "  systemctl start noc-copilot.service   # pulls in noc-lab.service (topology+telemetry+dataapi) first"
echo "  # or manually:  ./sim-up.sh  &&  ./copilot-up.sh"
echo "NOTE: copilot/.env uses hosted NVIDIA NIM — confirm the nvapi keys are valid + reachable."
echo "NOTE: RAG index isn't prebuilt — if /chat needs it: python3 -m copilot.retrieval.seed (COPILOT_KB_URI)."
