#!/usr/bin/env python3
"""Traffic generator — drives diurnal load across the lab's CE/host pairs.

Three backends, same diurnal curve shape:
  nc     — orchestrate real BusyBox-nc client/server flows between host containers
           via `docker exec`, shaped to the diurnal curve. Moves real bytes across
           the data plane so SNMP ifHCIn/OutOctets climb and nfacctd sees flows.
           DEFAULT when TRAFFICGEN_BACKEND=nc or running in compose.
  iperf3 — same but with iperf3 (not available on wbitt/network-multitool:alpine-minimal;
           kept for future use when hosts carry iperf3).
  sim    — Python socket flow simulator (loopback only, no real dataplane bytes).
           Useful for unit testing without the lab.

# ponytail: nc backend chosen over iperf3 — host image is
#   wbitt/network-multitool:alpine-minimal which has BusyBox nc but NOT iperf3.
#   nc is enough: `dd if=/dev/zero | nc -w<t> <sink_ip> <port>` sends real TCP
#   bytes across the MPLS/WireGuard overlay so ifHCInOctets visibly climbs and
#   nfacctd captures flows. One nc listener per (sink, VRF, port) restarted each
#   tick; connections are short-lived (one per flow plan row). Ceiling: no UDP DSCP
#   marking (BusyBox nc lacks --tos); DSCP is preserved on the real traffic only
#   if QoS marking happens inside the CE node (configured separately). Upgrade path:
#   add iperf3 to the frr-node image via the Dockerfile and switch backend to iperf3.

Per-VRF flow mix (per spec dscp classes):
  VOICE  small steady UDP-like  (EF,  many tiny flows, low variance)
  CORP   bursty TCP             (AF31, fewer, larger, spiky)
  GUEST  best-effort bulk       (BE,  occasional large transfers)

Run:
  python3 trafficgen.py --plan               # print the diurnal traffic plan as JSON lines and exit
  python3 trafficgen.py --backend nc         # drive real nc flows between lab hosts (default)
  python3 trafficgen.py --backend sim        # run the loopback socket simulator
  python3 trafficgen.py --backend iperf3     # print the iperf3 commands it WOULD run (dry)
  python3 trafficgen.py --selftest
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
import diurnal  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "controller"))
from topo import build_model  # noqa: E402

PERIOD_SECONDS = float(os.environ.get("DIURNAL_PERIOD", "3600"))
# Default backend: nc moves real dataplane bytes; sim is loopback-only.
DEFAULT_BACKEND = os.environ.get("TRAFFICGEN_BACKEND", "nc")

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


# --------------------------------------------------------------------------- nc backend
# ponytail: no iperf3 on wbitt/network-multitool:alpine-minimal, but BusyBox nc is
#   present. We drive real cross-site TCP flows with:
#     docker exec <sink>  nc -l -p <port>  (listener, exits after one connection)
#     docker exec <src>   sh -c "dd if=/dev/zero bs=<bs> count=<n> | nc -w3 <ip> <port>"
#   One listener is started per flow row; the sender connects, dumps bytes, closes.
#   Port base 19000 + row_index to avoid collisions across concurrent rows.
#   Bytes scaled to diurnal curve (bytes_per_flow * flows at the current hour).
#   This is enough to make ifHCInOctets climb on CE nodes and let nfacctd export flows.

LAB_NAME = os.environ.get("CLAB_LAB", "sdwan_mpls_noc")
NC_PORT_BASE = int(os.environ.get("NC_PORT_BASE", "19000"))
NC_FLOW_SCALE = float(os.environ.get("NC_FLOW_SCALE", "0.05"))  # fraction of plan bytes to send (keep it light)


def _clab(node):
    """Return full clab container name for a node short-name."""
    return f"clab-{LAB_NAME}-{node}"


def _host_cname(ce_node, vrf):
    """Container name for the host behind a CE node for a given VRF.

    Generator creates hosts as h_<ce_suffix>_<vrf_lower>, e.g.
    ce_branch1 + CORP -> h_branch1_corp.
    """
    suffix = ce_node[len("ce_"):] if ce_node.startswith("ce_") else ce_node
    return _clab(f"h_{suffix}_{vrf.lower()}")


def _host_eth1_ip(cname):
    """Read eth1 IP from a running container. Returns None on failure."""
    try:
        out = subprocess.run(
            ["docker", "exec", cname, "ip", "-4", "addr", "show", "eth1"],
            capture_output=True, text=True, timeout=3,
        ).stdout
        for tok in out.split():
            if "." in tok and "/" in tok:
                return tok.split("/")[0]
    except Exception:
        pass
    return None


def _nc_send_flow(src_cname, dst_ip, port, total_bytes):
    """Send total_bytes from src to dst via nc. Fire-and-forget; errors are silent."""
    # dd produces the bytes; nc pipes them to the listener.
    bs = 65536
    count = max(1, total_bytes // bs)
    cmd = f"dd if=/dev/zero bs={bs} count={count} 2>/dev/null | nc -w4 {dst_ip} {port}"
    try:
        subprocess.run(
            ["docker", "exec", src_cname, "sh", "-c", cmd],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass


def _nc_listen(sink_cname, port):
    """Start a one-shot nc listener on the sink (exits after first connection)."""
    try:
        subprocess.run(
            ["docker", "exec", sink_cname, "nc", "-l", "-p", str(port)],
            capture_output=True, timeout=35,
        )
    except Exception:
        pass


def run_nc(model, interval, ticks=None, fault_scale=None):
    """Drive real cross-site TCP flows using BusyBox nc via docker exec.

    For each tick: build the plan, pick one hub per VRF as the sink, start nc
    listeners on sinks, then launch senders from branch/dc spoke hosts.
    Bytes sent = plan_bytes * NC_FLOW_SCALE (default 5%) to stay light.
    """
    # Pre-resolve sink IPs: hub hosts per VRF.
    hub_nodes = [h["node"] for h in model["hubs"]]
    sink_ip_cache = {}  # (hub_node, vrf) -> ip

    def _sink_ip(hub_node, vrf):
        key = (hub_node, vrf)
        if key not in sink_ip_cache:
            cname = _host_cname(hub_node, vrf)
            ip = _host_eth1_ip(cname)
            sink_ip_cache[key] = ip  # cache None too (no retry noise)
        return sink_ip_cache[key]

    print(json.dumps({"event": "trafficgen_up", "backend": "nc",
                      "scale": NC_FLOW_SCALE, "period_s": PERIOD_SECONDS,
                      "lab": LAB_NAME}), flush=True)
    i = 0
    while ticks is None or i < ticks:
        now = time.time()
        plan = build_plan(now, model, fault_scale)
        threads = []
        tick_bytes = 0

        # Group plan by VRF to pick one sink hub per VRF (round-robin across hubs).
        for row_idx, p in enumerate(plan):
            if p["flows"] == 0:
                continue
            vrf = p["vrf"]
            # Sink: cycle through hubs by row_idx
            hub = hub_nodes[row_idx % len(hub_nodes)]
            sink_ip = _sink_ip(hub, vrf)
            if sink_ip is None:
                continue  # hub host not present for this VRF (e.g. branch-only VRF)
            sink_cname = _host_cname(hub, vrf)
            src_cname = _host_cname(p["site"], vrf)
            port = NC_PORT_BASE + row_idx
            nbytes = max(1024, int(p["bytes_per_flow"] * p["flows"] * NC_FLOW_SCALE))
            tick_bytes += nbytes

            # Start listener then sender concurrently.
            lt = threading.Thread(target=_nc_listen, args=(sink_cname, port), daemon=True)
            lt.start()
            time.sleep(0.05)  # give listener time to bind
            st = threading.Thread(target=_nc_send_flow,
                                  args=(src_cname, sink_ip, port, nbytes), daemon=True)
            st.start()
            threads.append((lt, st))

        # Wait for all senders (listeners self-exit after one connection).
        for lt, st in threads:
            st.join(timeout=35)

        total_offered = sum(p["offered_bps"] for p in plan)
        print(json.dumps({"event": "tick", "hod": plan[0]["hod"] if plan else None,
                          "offered_bps_total": total_offered,
                          "nc_bytes_sent": tick_bytes,
                          "active_rows": len(threads)}), flush=True)
        i += 1
        time.sleep(interval)


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
    ap.add_argument("--backend", choices=["sim", "iperf3", "nc"], default=DEFAULT_BACKEND)
    ap.add_argument("--interval", type=float, default=30.0,
                    help="seconds between ticks (default 30 for nc backend)")
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
        # Dry: print the commands it would run (iperf3 not present on lab hosts).
        for c in iperf3_commands(time.time(), model):
            print(c)
        return
    if args.backend == "nc":
        run_nc(model, args.interval, args.ticks)
        return
    run_sim(model, args.interval, args.ticks)


if __name__ == "__main__":
    main()
