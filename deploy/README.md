# deploy — move the whole lab to another machine

The lab is **not** reproducible from git alone (topology, docker images, `.env`, WG keys,
datasets are all gitignored). This kit packages the live working dir + Claude context and
rebuilds it on a bare Debian 12 host.

## What moves
- **`/root/LAB`** — whole dir: code, generated `topology/` (384 configs), `airgap/images/`
  (3 local-built images + registry incl kafka), `copilot/.env` (nvapi keys), WG keys, datasets, `ledger.db`.
- **`~/.claude` + `~/.claude.json`** — chats (`projects/`), skills+plugins, settings, prompt
  history, credentials. Ephemeral caches/logs/session-state excluded.

## Source machine (this one) — non-destructive, reads only
```bash
./deploy/package.sh            # → /root/noc-lab-bundle-*.tar.gz + /root/claude-context-*.tar.gz
# ./deploy/package.sh --lean       drop regenerable giants (synthetic/output, dataapi/datasets)
# ./deploy/package.sh --no-claude  lab only
```
Copy both tarballs to the target over a **private channel** — they contain nvapi keys, WG
private keys, and Claude oauth tokens.

## Target machine (bare Debian 12, needs internet for provisioning)
```bash
sudo ./deploy/provision-debian.sh                       # docker, containerlab, python, kernel modules+sysctls, Phase-0 verify
tar xzf "$(ls -t noc-lab-bundle-*.tar.gz | head -1)" -C /root   # → /root/LAB  (MUST be /root/LAB — paths hard-coded)
# claude context: let restore.sh handle it (it picks the newest bundle + backs up any existing ~/.claude)
/root/LAB/deploy/restore.sh                             # docker load, pip deps, systemd enable, claude restore
systemctl start noc-copilot.service                     # pulls in noc-lab.service first
```
Use an explicit filename if several bundles sit in `/root` — a bare `*.tar.gz` glob with two
matches feeds the extras to `tar` as member names and fails.

## Gotchas
- **Path is fixed at `/root/LAB`** — `.service` files + several scripts hard-code it.
- **Kernel** — MPLS/VRF dataplane needs `CONFIG_LWTUNNEL` + `CONFIG_MPLS_IPTUNNEL` +
  `CONFIG_NET_VRF`. `provision-debian.sh` runs the Phase-0 checks and warns if absent; control-plane
  telemetry still works without them. See `docs/PHASE0ENVIRONMENT.md`.
- **NIM is hosted** — `copilot/.env` points at NVIDIA-hosted endpoints; target needs network +
  valid `nvapi-…` keys. Not air-gapped.
- **RAG index** isn't prebuilt — re-seed from tracked `ragcorpus/` if `/chat` needs it:
  `python3 -m copilot.retrieval.seed`.
- **`--lean` bundles** ship no datasets — the target comes up with an empty data plane.
  Regenerate on the target after the lab is running: `python3 dataapi/export.py` (live captures)
  and/or `python3 synthetic/generate.py` (synthetic parquet).
- **Air-gapped target?** provisioning assumes internet (apt + docker/containerlab install scripts).
  For a truly offline target, pre-stage the `.deb`s + python wheels (`pip install --no-index
  --find-links wheels/`) — not covered here.
