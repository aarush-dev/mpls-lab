# 09 — Deployment & Demo

**How to stand up the whole system, prove it is air-gapped (cut off from the internet), and show it working.**

← [08 Integrated System](08_INTEGRATED_SYSTEM.md) · → [10 Future Prospects](10_FUTURE_PROSPECTS.md)

---

## 1. Deployment model

The system deploys in three layers. Each layer has its own startup script and a systemd (Linux service manager) unit for autostart:

```
┌─ Layer 3: Copilot ──────────  copilot-up.sh  ← noc-copilot.service
│    /chat API :8100 · PA-emulator predictor · forensic trigger
├─ Layer 2: Telemetry ────────  (docker compose, driven by sim-up.sh)
│    VictoriaMetrics · Grafana · Loki · Telegraf · nfacctd · Kafka · sidecars
├─ Layer 1: Network lab ──────  sim-up.sh  ← noc-lab.service
│    148-container Containerlab SD-WAN/MPLS topology
└─ Layer 0: Host prereqs ─────  docs/PHASE0ENVIRONMENT.md (run once)
```

`noc-copilot.service` `Requires`/`After` `noc-lab.service`. This means the copilot never starts before its data source does (`noc-copilot.service:9-24`). Both units are `oneshot`/`RemainAfterExit`, targeting `multi-user.target`.

---

## 2. Layer 0 — host prerequisites

Run the Phase 0 checklist once on a fresh host (`docs/PHASE0ENVIRONMENT.md`). Host of record: **19
cores / 108 GB RAM / 1007 GB disk**, kernel `6.18…-WSL2` (all five Phase-0 checks passed 2026-07-26,
`PHASE0ENVIRONMENT.md:57-59`).

| Requirement | Detail | Cite |
|---|---|---|
| Kernel modules | `mpls_router`, `mpls_gso`, `mpls_iptunnel`, VRF, netem (+`dummy`), veth (virtual network cable pair), wireguard | `PHASE0ENVIRONMENT.md:21,43-55` |
| Sysctls (kernel settings) | `net.mpls.platform_labels=1048575`; inotify limits | `PHASE0ENVIRONMENT.md:33,124` |
| Post-WSL-restart | `bridge`, `br_netfilter`, `ip6_tables` for dockerd; `cls_u32` for QoS | `PHASE0ENVIRONMENT.md:92-109` |

> **Kernel caveat, plainly stated.** On this WSL2 kernel, `mpls_router` loads but that's a **false pass**: it looks fine, but MPLS
> label imposition (tagging packets for MPLS routing) needs `CONFIG_LWTUNNEL`, which this kernel doesn't have (`Error: CONFIG_LWTUNNEL is not
> enabled`). Result: the VPNv4 dataplane (the part that actually forwards VPN traffic) only half-works — pe1 installs 9 of 114 routes in its forwarding table, and iBGP
> VPNv4 gets stuck in the "Connect" state (`bgp_peer_established=0`). **The OSPF/LDP control plane and its telemetry
> still work for real**; only VRF forwarding is affected. A `vrflite` fallback is named as a fix but hasn't been built
> (`PHASE0ENVIRONMENT.md:61-85`).

---

## 3. Layers 1–3 — bring-up

### Autostart (recommended)
```bash
sudo cp noc-lab.service noc-copilot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now noc-lab.service noc-copilot.service
```

### `sim-up.sh` — network + telemetry (`sim-up.sh`)
Safe to re-run. Its key trick is **detecting an unwired lab**: a host or docker restart keeps the 148
containers alive but destroys the Containerlab veths (only `eth0`/`lo` survive), which silently kills the
control plane. `wired()` checks `p1:eth1`; if the veths are gone it runs `containerlab destroy
--cleanup && deploy` to re-wire everything and re-run the setup hooks (`sim-up.sh:14-20`). It then starts
the telemetry docker compose stack (minus the native Grafana — the plugin stack owns port :3000), the controller sidecars,
and the Data API (`workers=1`, because the fault registry lives in one process). It checks that dataapi
`/topology`, VM `:8428/health`, Loki `:3100/ready`, and the plugin Grafana are all healthy before continuing (`sim-up.sh:60-64`).

### `copilot-up.sh` — the intelligence layer (`copilot-up.sh`)
Starts exactly three processes sharing one ledger file (`COPILOT_LEDGER_PATH=ledger.db`): the `/chat`
API (`uvicorn copilot.api.app:app --port 8100`, uvicorn is the web server that runs the API), the PA-emulator predictor
(`python3 -m copilot.emulator.predictor`), and the forensic trigger
(`python3 -m copilot.forensic.trigger`). It fails early if the Data API isn't serving, and runs
a **heartbeat check** — it injects a fake low-severity label and requires a fresh Prediction
Record within one interval — to prove the predictor-to-ledger path actually works (`copilot-up.sh:35-113`).

### Manual regen + deploy
```bash
cd generator && python3 generate.py            # spec → clab.yml + 148 node configs
cd ../topology && sudo containerlab deploy -t clab.yml
cd ../telemetry && docker compose up -d
cd ../dataapi && bash start.sh                  # :8000, single worker
cd .. && ./copilot-up.sh                        # :8100 + predictor + trigger
```

---

## 4. Ports, URLs, credentials

| Service | URL | Port | Cite |
|---|---|---|---|
| Grafana app plugin | `http://localhost:3000/a/mplslab-noccopilot-app` | 3000 | `docs/04:993` |
| Grafana (native telemetry) | `http://172.20.20.51:3000` | 3000 | `docs/04:985` |
| VictoriaMetrics | `http://172.20.20.50:8428` | 8428 | `docs/04:986` |
| Loki | `http://172.20.20.54:3100` | 3100 | `docs/04:987` |
| Controller (Prometheus) | `http://172.20.20.56:9362` | 9362 | `docs/04:990` |
| Kafka | `172.20.20.60:9092` (in-lab) / `127.0.0.1:29092` (host) | 9092/29092 | `docs/04:995` |
| **Data API** | `http://127.0.0.1:8000` | 8000 | `dataapi/app.py` |
| **Copilot `/chat`** | `http://127.0.0.1:8100` | 8100 | `copilot/api/app.py:11` |

> **Credentials, corrected.** Grafana has **no password**. It logs you in automatically as Admin
> with the login form turned off (`GF_AUTH_ANONYMOUS_ENABLED:true`) — it is *not* `admin/admin` as older docs
> say (`docker-compose.yml:45-47`). The real security boundary is the **CORS allow-list** (CORS = the browser rule for which sites may call an API): both the
> Data API and the copilot API only accept requests from `localhost:3000` / `127.0.0.1:3000`, and only GET/POST — that
> is the auth model per the competition brief (`dataapi/app.py:41`, `copilot/api/app.py:46`).

---

## 5. Air-gap packaging and verification

Air-gap (no internet access) is the graded constraint worth 20%. Three scripts under `airgap/` implement it:

| Script | Action | Cite |
|---|---|---|
| `pull-and-save.sh` | `docker save \| xz` each image → `images/*.tar.xz`; writes `manifest.txt` | `pull-and-save.sh:57-90` |
| `load-offline.sh` | `xz -d \| docker load`; checks all expected tags are present | `load-offline.sh:29-71` |
| `verify-airgap.sh` | 4 checks; expects **`PASS: 4 FAIL: 0` → "AIR-GAP VERIFIED"** | `verify-airgap.sh:129-134` |

**The four checks** (`verify-airgap.sh`): (1) every `clab.yml` image is set to `image-pull-policy: Never`;
(2) every required image is already stored locally, so compose has nothing to pull; (3) a 30-second `tcpdump` (packet capture tool)
run with `-i any` shows **zero** packets going from a container to the public internet — `-i any` matters here because it
captures traffic before MASQUERADE/NAT rewrites it; (4) zero `docker pull` events since start. Bundle = **13 images, ~619 MB**
(`manifest.txt`); every compose/lab image uses a pinned tag with `pull_policy: never` — no `:latest` tags.

> **Two corrections to older docs.** The verifier reports **4/4**, not "14/14" — there's no
> 14-check version anywhere in the source. The bundle is **13 images**, not 11. And the verifier's
> tcpdump check is currently **documented only**, meaning it hasn't been run live against a running lab: the last recorded run
> happened with the lab down (`docs/04:791`). The static checks (pull-policy, image presence, pull
> events) are solid and pass; the live zero-traffic capture still needs a run against a live lab to count as a recorded
> pass.

> **⚠️ Air-gap is not fully closed — the LLM call still leaves the lab.** The copilot's default `llm_profile: nim` and its
> embedder (the model that turns text into search vectors) run **over the network on NVIDIA's hosted service** (`config.yaml:7`, `e2e/harness.py:8-9`). The air-gap
> verifier only checks the *lab + telemetry* containers, not the copilot's LLM calls. Closing this gap means
> switching to the local `unsloth-local` profile instead — that's future work (see doc 10).

---

## 6. Verification commands

| Concern | Command | Cite |
|---|---|---|
| Control plane | `vtysh -c "show bgp ipv4 vpn summary"`, `"show bfd peers brief"`, `ip link show wg0` | `docs/04:355-359,932-937` |
| Telemetry flowing | VM `interface_ifHCInOctets` count > 0; Grafana `/api/health`; Loki `/ready` | `docs/04:78-88,879` |
| Inject a fault | `python3 faults/orchestrator.py --scenario <t> --target <d> --severity high --duration <s>` (`--demo` = congestion/ce_branch1) | `docs/04:200-227` |
| Data API | `/metrics /events /flows /labels /topology /datasets /faults/*` | `docs/04:366-471` |
| Air-gap | `cd airgap && ./verify-airgap.sh` | `docs/04:788` |

**Recorded live-deploy verifications** (from `HANDOFF.md`):

- **2026-07-26** — full 148-container lab + telemetry deployed; `env-metrics` exercised (3 bugs
  fixed); real dataset exported; `profile.json` recalibrated.
- **2026-08-03 (#39)** — lab found *unwired* (this drove the `sim-up.sh` fix mentioned above); after
  destroy+redeploy: OSPF 6 Full on p1, LDP 6 operational, BGP-VPNv4 11 established on pe1, WireGuard
  6 peers on ce_branch1; VM showed 168 tunnel series (age 0 s), 70 SNMP nodes; one scenario ran end-to-end
  (`ldp_session_flap` on pe1), writing label row 70 and reaching `/events`.

---

## 7. The demo

### End-to-end copilot proof (recorded)
`copilot/e2e/harness.py` runs 7 scripted questions through the whole chat path against real backends:

```bash
COPILOT_E2E_LIVE=1 python3 -m copilot.e2e.harness   # writes copilot/e2e/REPORT.md + traces/
```
Recorded run (`REPORT.md:3-15`): `openai/gpt-oss-20b`, effort `high`, over the NVIDIA endpoint —
Q3 (flows), Q4 (topology blast-radius), Q5 (KB runbook) returned **cited answers**; Q1 correctly reported "what's missing"; Q6 stopped at `step_cap`; Q7 correctly asked a follow-up question. Every read/KB tool call returned
real rows; nothing crashed.

### The operator story (inject → predict → explain)
There's no single wrapper script for this — the demo is put together from separate pieces:

1. **Inject** a fault — via the CLI `orchestrator.py`, the Data API `/faults/inject`, or the Grafana plugin's
   **Fault Injection page** (which shows the status moving from pending → predicted → down on-screen).
2. **See the precursor (the early warning sign)** — the `sdwan_tunnel_latency_ms` metric ramps up in Grafana before the impact hits
   (`docs/04:118-119,220`).
3. **Autonomous forensic case** — the predictor writes a Prediction Record, the forensic trigger
   fires when `alert==true`, freezes the time window, and writes `case.md` (`copilot-up.sh:2-4`).
4. **Cited answer** — ask the copilot via the Grafana Copilot tab (or `/chat` directly); the streamed trace
   shows each tool call it made, and every claim links back to a real row via a `[source:offset]` citation.

### UI build/run
```bash
cd "grafana ui/plugin"
node ./node_modules/webpack/bin/webpack.js -c ./.config/webpack/webpack.config.ts --env production
cd .. && docker compose -p noc-plugin up -d
```
Verified green: the production webpack build, `tsc --noEmit` (TypeScript type check with no file output), and the jest test suite all pass
(`grafana ui/README.md:85`). The Data API must be reachable at `127.0.0.1:8000`, and Grafana must run
on port 3000 (the CORS origin).

---

## 8. Deployment status summary

| Capability | Status |
|---|---|
| Full lab + telemetry deploy | ✅ verified live (2026-07-26, 2026-08-03) |
| Autostart + unwired-lab self-heal | ✅ built + verified |
| Copilot stack (`/chat` + predictor + trigger) | ✅ built; heartbeat-verified |
| Air-gap static checks (pull-policy, images, pinned tags) | ✅ pass |
| Air-gap runtime zero-egress capture | ⚠️ documented; last run had lab down |
| Air-gap on copilot LLM/embedder | ❌ open — hosted `nim` profile |
| MPLS VPNv4 dataplane on WSL2 | ⚠️ partial (`CONFIG_LWTUNNEL` absent); control plane real |

**Next:** [10 — Future Prospects](10_FUTURE_PROSPECTS.md), what remains to build and the known-open
items.
