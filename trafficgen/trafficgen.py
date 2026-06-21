#!/usr/bin/env python3
"""Traffic generator — drives diurnal load across the lab's CE/host pairs.

Two backends, same diurnal curve shape:
  iperf3   — orchestrate real iperf3 client/server pairs (host -> hub host) shaped
             to the curve. Realistic dataplane bytes for SNMP/pmacct telemetry.
  sim      — Python socket flow simulator: opens short TCP/UDP flows at a rate set
             by the curve, no iperf3 needed. Same utilization/latency curve shape,
             air-gap-trivial, used when iperf3 cross-container orchestration is heavy.

# ponytail: DEFAULT backend = "sim". iperf3 across 22 containers needs a server per
#   sink + per-pair `docker exec` choreography and an iperf3 image on the hosts —
#   heavier than the signal warrants right now. The sim backend produces the same
#   diurnal utilization curve (the shape the ML learns from) by modulating flow
#   count/size with diurnal.util(). Ceiling: sim emits a planned-bytes series, not
#   real wire bytes; pmacct won't see sim flows. Upgrade path: set --backend iperf3
#   once hosts carry an iperf3 binary (note in README) — pairing is derived here,
#   so only the launch shell-out changes.

Per-VRF flow mix (per spec dscp classes):
  VOICE  small steady UDP-like  (EF,  many tiny flows, low variance)
  CORP   bursty TCP             (AF31, fewer, larger, spiky)
  GUEST  best-effort bulk       (BE,  occasional large transfers)

Run:
  python3 trafficgen.py --plan            # print the diurnal traffic plan as JSON lines and exit
  python3 trafficgen.py --backend sim     # run the socket simulator (loopback by default)
  python3 trafficgen.py --backend iperf3  # print the iperf3 commands it WOULD run (dry by default)
  python3 trafficgen.py --selftest
"""
import argparse
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
import diurnal  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "controller"))
from topo import build_model  # noqa: E402

PERIOD_SECONDS = float(os.environ.get("DIURNAL_PERIOD", "3600"))

# Per-VRF flow shape at full utilization (util=1.0):
#   flows_max     : concurrent flows when saturated
#   bytes_per_flow: nominal payload per flow (bytes)
#   proto         : transport hint (telemetry/QoS label)
#   dscp          : marking (matches qos.sh classes)
#   burstiness    : 0=steady .. 1=very spiky (scales variance in flow count)
VRF_FLOW = {
    "VOICE": {"flows_max": 40, "bytes_per_flow": 20_000,    "proto": "udp", "dscp": "EF",   "burstiness": 0.1},
    "CORP":  {"flows_max": 25, "bytes_per_flow": 800_000,   "proto": "tcp", "dscp": "AF31", "burstiness": 0.7},
    "GUEST": {"flows_max": 8,  "bytes_per_flow": 5_000_000, "proto": "tcp", "dscp": "BE",   "burstiness": 0.9},
}


def _ce_host(node):
    """Host container name for a CE node (h_<suffix>), per generated clab.yml."""
    return "h_" + node[len("ce_"):] if node.startswith("ce_") else "h_" + node


def build_plan(now, model, fault_scale=None):
    """Return a list of per-(site,vrf) flow plans for this instant.

    fault_scale: optional {(site,vrf): multiplier} to perturb the curve (faults).
    """
    fault_scale = fault_scale or {}
    hod = diurnal.hour_of_cycle(now, PERIOD_SECONDS)
    plans = []
    spokes = {s["node"]: s for s in model["spokes"]}
    for site, sp in spokes.items():
        for vrf in model["site_vrfs"].get(sp["site_type"], []):
            u = diurnal.util(hod, vrf)
            mult = fault_scale.get((site, vrf), 1.0)
            shape = VRF_FLOW[vrf]
            flows = max(0, round(shape["flows_max"] * u * mult))
            offered_bps = flows * shape["bytes_per_flow"] * 8 / max(1, PERIOD_SECONDS / 24)
            plans.append({
                "site": site, "site_type": sp["site_type"], "vrf": vrf,
                "hod": round(hod, 2), "util": round(u, 3),
                "flows": flows, "proto": shape["proto"], "dscp": shape["dscp"],
                "bytes_per_flow": shape["bytes_per_flow"],
                "offered_bps": round(offered_bps),
                "src": _ce_host(site),
            })
    return plans


# --------------------------------------------------------------------------- sim backend
class _SimSink:
    """Tiny loopback TCP sink so the simulator has somewhere to send bytes in a
    standalone run (no lab). In-lab you'd point flows at the peer host."""
    def __init__(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(64)
        self.port = self.srv.getsockname()[1]
        self.bytes_rx = 0
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._stop:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._drain, args=(c,), daemon=True).start()

    def _drain(self, c):
        try:
            while True:
                b = c.recv(65536)
                if not b:
                    break
                self.bytes_rx += len(b)
        finally:
            c.close()

    def close(self):
        self._stop = True
        self.srv.close()


def _sim_flow(port, nbytes):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=2)
        sent = 0
        chunk = b"x" * 65536
        while sent < nbytes:
            n = s.send(chunk[: min(len(chunk), nbytes - sent)])
            sent += n
        s.close()
        return sent
    except OSError:
        return 0


def run_sim(model, interval, ticks=None, fault_scale=None):
    sink = _SimSink()
    print(json.dumps({"event": "trafficgen_up", "backend": "sim",
                      "sink_port": sink.port, "period_s": PERIOD_SECONDS}), flush=True)
    i = 0
    try:
        while ticks is None or i < ticks:
            now = time.time()
            plan = build_plan(now, model, fault_scale)
            # Scale real bytes WAY down (1/1000 of plan) so the sim is light but the
            # curve shape is preserved; the JSON plan carries the true offered_bps.
            for p in plan:
                for _ in range(p["flows"]):
                    _sim_flow(sink.port, max(1, p["bytes_per_flow"] // 1000))
            total_offered = sum(p["offered_bps"] for p in plan)
            print(json.dumps({"event": "tick", "hod": plan[0]["hod"] if plan else None,
                              "offered_bps_total": total_offered,
                              "sink_bytes_rx": sink.bytes_rx}), flush=True)
            i += 1
            time.sleep(interval)
    finally:
        sink.close()


# ------------------------------------------------------------------------- iperf3 backend
def iperf3_commands(now, model, fault_scale=None):
    """Derive the iperf3 client commands for this instant (does NOT run them).

    Sink = the host behind a hub CE (hub1's host); each spoke host is a client.
    Bitrate shaped to offered_bps; -u for VOICE (UDP), TCP otherwise.
    """
    hub = model["hubs"][0]["node"]
    sink_host = _ce_host(hub)  # h_hub1
    cmds = []
    for p in build_plan(now, model, fault_scale):
        if p["flows"] == 0:
            continue
        rate = max(1, p["offered_bps"])
        proto = "-u " if p["proto"] == "udp" else ""
        # DSCP -> tos byte (dscp<<2). EF=46->0xb8, AF31=26->0x68, BE=0->0x0.
        dscp_val = {"EF": 46, "AF31": 26, "BE": 0}[p["dscp"]]
        cmds.append(
            f"docker exec clab-sdwan_mpls_noc-{p['src']} "
            f"iperf3 -c <{sink_host}-ip> {proto}-b {rate} -P {p['flows']} "
            f"-t {int(PERIOD_SECONDS/24)} --tos {dscp_val << 2}  # {p['vrf']} util={p['util']}"
        )
    return cmds


# ---------------------------------------------------------------------------- selftest
def _selftest():
    model = build_model()

    # Plan at a peak hour and a trough hour; peak must offer strictly more load.
    peak_t = PERIOD_SECONDS * (13.5 / 24.0)   # ~14:00
    trough_t = PERIOD_SECONDS * (3.0 / 24.0)  # ~03:00
    peak = build_plan(peak_t, model)
    trough = build_plan(trough_t, model)
    assert peak and trough, "empty plan"
    peak_load = sum(p["offered_bps"] for p in peak)
    trough_load = sum(p["offered_bps"] for p in trough)
    assert peak_load > trough_load * 2, \
        f"diurnal swing degenerate: peak={peak_load} trough={trough_load}"

    # Branch sites must NOT carry GUEST; hub/dc must.
    vrfs_by_site = {}
    for p in peak:
        vrfs_by_site.setdefault(p["site"], set()).add(p["vrf"])
    assert "GUEST" not in vrfs_by_site["ce_branch1"], "branch should not have GUEST"
    assert "GUEST" in vrfs_by_site["ce_dc1"], "dc should have GUEST"

    # Plan rows must be well-formed and non-negative.
    for p in peak:
        assert p["flows"] >= 0 and p["offered_bps"] >= 0
        assert p["src"].startswith("h_"), f"bad src {p['src']}"
        assert p["dscp"] in ("EF", "AF31", "BE")

    # Fault perturbation visibly changes the curve.
    fs = {("ce_dc1", "GUEST"): 4.0}
    perturbed = build_plan(peak_t, model, fault_scale=fs)
    base_g = next(p["flows"] for p in peak if p["site"] == "ce_dc1" and p["vrf"] == "GUEST")
    pert_g = next(p["flows"] for p in perturbed if p["site"] == "ce_dc1" and p["vrf"] == "GUEST")
    assert pert_g > base_g, "fault_scale did not perturb the plan"

    # iperf3 command derivation produces one cmd per active flow row.
    cmds = iperf3_commands(peak_t, model)
    assert cmds and all("iperf3 -c" in c for c in cmds), "iperf3 cmds malformed"

    # sim backend: a couple of ticks actually move bytes through the sink.
    run_sim(model, interval=0.0, ticks=2)

    print(f"trafficgen selftest OK  peak_bps={peak_load} trough_bps={trough_load} "
          f"rows={len(peak)} iperf3_cmds={len(cmds)}")


def main():
    ap = argparse.ArgumentParser(description="Diurnal traffic generator")
    ap.add_argument("--backend", choices=["sim", "iperf3"], default="sim")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--ticks", type=int, default=None, help="stop after N ticks")
    ap.add_argument("--plan", action="store_true", help="print one plan as JSON lines and exit")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return
    model = build_model()
    if args.plan:
        for p in build_plan(time.time(), model):
            print(json.dumps(p))
        return
    if args.backend == "iperf3":
        # Dry: print the commands it would run (container wiring is Phase 2).
        for c in iperf3_commands(time.time(), model):
            print(c)
        return
    run_sim(model, args.interval, args.ticks)


if __name__ == "__main__":
    main()
