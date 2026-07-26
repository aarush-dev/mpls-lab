#!/usr/bin/env python3
"""env-metrics.py -- device health sidecar: real resource/queue/routing telemetry
plus modelled environmental sensors. Prometheus text on stdout.

Run the same way as ldp-metrics.sh (see telemetry/docker-compose.yml): the
noc-env-metrics container loops this and POSTs stdout to VictoriaMetrics at
/api/v1/import/prometheus.

WHAT IS REAL vs MODELLED -- this distinction matters for anyone training on it:

  REAL (measured from the running lab)
    node_cpu_pct{device}              docker stats CPU%     (control-plane load)
    node_mem_pct{device}              docker stats MEM%
    iface_queue_backlog_bytes{...}    tc -s qdisc backlog   (congestion precursor)
    iface_queue_drops{...}            tc -s qdisc drops
    bgp_msg_rx_total{device}          vtysh show bgp summary json
    bgp_msg_tx_total{device}
    rib_routes{device}                vtysh show ip route summary json
    ospf_lsa_count{device}            vtysh show ip ospf database json

  MODELLED (containers have no sensors -- see telemetry/envmodel.py for the
  literature each model shape comes from)
    device_temp_c{device}             chassis temperature
    device_power_watts{device}        chassis power draw
    device_fan_rpm{device}            fan speed
    device_psu_voltage_v{device}      12V rail
    xcvr_temp_c{device,interface}     SFF-8472 transceiver temperature
    xcvr_rx_power_dbm{device,interface}
    xcvr_tx_bias_ma{device,interface} laser bias -- the laser-ageing precursor

The modelled sensors are driven by MEASURED CPU load, not by a free-running
clock, so their correlation with real device state is genuine even though the
transfer function is synthetic.

# ponytail: one sidecar for all of it. Separate scripts per signal would mean
#   separate docker exec storms over the same 70 nodes; this walks them once.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import envmodel  # noqa: E402

CLAB_PREFIX = "clab-sdwan_mpls_noc"
STATE_PATH = os.environ.get("ENV_STATE", "/tmp/env-metrics-state.json")

P_NODES = [f"p{i}" for i in range(1, 25)]
PE_NODES = [f"pe{i}" for i in range(1, 13)]
CE_NODES = ([f"ce_branch{i}" for i in range(1, 25)]
            + [f"ce_hub{i}" for i in range(1, 7)]
            + [f"ce_dc{i}" for i in range(1, 5)])
ALL_NODES = P_NODES + PE_NODES + CE_NODES
ROUTING_NODES = P_NODES + PE_NODES

# POP membership drives the SHARED per-POP ambient temperature. Read from the
# generator's topology-meta so it tracks the real layout; falls back to a
# 4-P-per-POP split matching the current spec.
META_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "topology", "topology-meta.json")


def _diurnal_util():
    """Current offered-load fraction from the shared diurnal curve.

    Same curve trafficgen and the controller use, so modelled chassis load lines
    up with the traffic actually on the wire. Falls back to a mid load if the
    module is not importable (sidecar image without trafficgen mounted).
    """
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trafficgen"))
        import diurnal
        period = float(os.environ.get("DIURNAL_PERIOD", "3600"))
        return diurnal.util(diurnal.hour_of_cycle(time.time(), period))
    except Exception:
        return 0.5


def _sh(args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def dexec(device, *cmd, timeout=10):
    return _sh(["docker", "exec", f"{CLAB_PREFIX}-{device}", *cmd], timeout=timeout)


def vtj(device, cmd):
    """vtysh JSON query -> dict ({} on any failure)."""
    out = dexec(device, "vtysh", "-c", cmd)
    try:
        return json.loads(out)
    except Exception:
        return {}


# --- POP map ----------------------------------------------------------------
def pop_index_map():
    """device -> POP index (1-based). Empty dict if topology-meta is unavailable."""
    try:
        with open(META_PATH) as f:
            m = json.load(f)
    except Exception:
        return {}
    out = {}
    for pop, members in (m.get("pops") or {}).items():
        try:
            k = int(str(pop).replace("pop", ""))
        except ValueError:
            continue
        for d in members:
            out[d] = k
    for pe, pop in (m.get("pe_pop") or {}).items():
        try:
            out[pe] = int(str(pop).replace("pop", ""))
        except (ValueError, TypeError):
            pass
    return out


def _fallback_pop(device):
    """POP index when topology-meta is absent: 4 P per POP, 2 PE per POP."""
    d = str(device)
    if d.startswith("pe"):
        return (int(d[2:]) - 1) // 2 + 1
    if d.startswith("p"):
        return (int(d[1:]) - 1) // 4 + 1
    return 1  # CEs sit at customer sites; treat as one ambient zone


# --- REAL: container CPU / memory -------------------------------------------
def docker_stats():
    """device -> (cpu_pct, mem_pct) for every lab container, in ONE call."""
    out = _sh(["docker", "stats", "--no-stream", "--format",
               "{{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}"], timeout=60)
    stats = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0].startswith(CLAB_PREFIX):
            continue
        dev = parts[0][len(CLAB_PREFIX) + 1:]
        try:
            stats[dev] = (float(parts[1].rstrip("%")), float(parts[2].rstrip("%")))
        except ValueError:
            continue
    return stats


# --- REAL: queue depth / drops from tc ---------------------------------------
def queue_stats(device, iface):
    """(backlog_bytes, drops) summed over the qdisc tree on <device>:<iface>.

    This is the most direct congestion precursor available: the queue fills
    BEFORE latency rises and long before loss starts.
    """
    out = dexec(device, "tc", "-s", "qdisc", "show", "dev", iface)
    backlog = drops = 0
    toks = out.split()
    for i, tk in enumerate(toks):
        if tk == "backlog" and i + 1 < len(toks):
            v = toks[i + 1]
            backlog += _bytes(v)
        if tk == "dropped" and i + 1 < len(toks):
            try:
                drops += int(toks[i + 1].rstrip(","))
            except ValueError:
                pass
    return backlog, drops


def _bytes(v):
    """Parse a tc size token: '0b', '13Kb', '1Mb' -> bytes."""
    v = v.rstrip(",")
    mult = 1
    if v.endswith("Kb"):
        mult, v = 1024, v[:-2]
    elif v.endswith("Mb"):
        mult, v = 1024 * 1024, v[:-2]
    elif v.endswith("b"):
        v = v[:-1]
    try:
        return int(float(v) * mult)
    except ValueError:
        return 0


# --- REAL: routing control-plane churn ---------------------------------------
def bgp_msg_counts(device):
    """(rx, tx) BGP message counters summed across peers -- churn rate signal."""
    d = vtj(device, "show bgp summary json")
    rx = tx = 0
    for fam in d.values():
        if not isinstance(fam, dict):
            continue
        for p in (fam.get("peers") or {}).values():
            if isinstance(p, dict):
                rx += int(p.get("msgRcvd") or 0)
                tx += int(p.get("msgSent") or 0)
    return rx, tx


def rib_routes(device):
    """Total installed routes -- drops on withdrawal storms, grows on reconverge."""
    d = vtj(device, "show ip route summary json")
    for r in (d.get("routes") or []):
        if str(r.get("type")) == "Totals":
            return int(r.get("rib") or 0)
    return sum(int(r.get("rib") or 0) for r in (d.get("routes") or []))


def ospf_lsa_count(device):
    """LSDB size -- churns during area flaps and reconvergence."""
    d = vtj(device, "show ip ospf database json")
    n = 0
    for k, v in d.items():
        if isinstance(v, dict) and k.lower().endswith("lsa"):
            n += len(v)
        elif isinstance(v, list):
            n += len(v)
    return n


# --- modelled-sensor state (thermal mass needs the previous value) -----------
def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def main():
    import random
    rng = random.Random()

    state = load_state()
    temps = state.get("temps", {})
    # Service-life fraction for optics: advances slowly across runs so laser bias
    # has a long-horizon upward trend (the SFF-8472 ageing precursor).
    age = float(state.get("age_frac", 0.0))
    age = min(1.0, age + 1.0 / (86400.0 / 30.0 * 30.0))  # ~30 simulated days

    pops = pop_index_map()
    stats = docker_stats()
    out = []

    def emit(name, help_, typ, rows):
        if not rows:
            return
        out.append(f"# HELP {name} {help_}")
        out.append(f"# TYPE {name} {typ}")
        out.extend(rows)

    # ---- REAL: cpu / mem ----------------------------------------------------
    emit("node_cpu_pct", "Container CPU percent (control-plane load)", "gauge",
         [f'node_cpu_pct{{device="{d}"}} {stats[d][0]:.3f}'
          for d in ALL_NODES if d in stats])
    emit("node_mem_pct", "Container memory percent", "gauge",
         [f'node_mem_pct{{device="{d}"}} {stats[d][1]:.3f}'
          for d in ALL_NODES if d in stats])

    # ---- REAL: queue backlog / drops on CE uplinks ---------------------------
    q_rows_b, q_rows_d = [], []
    for d in CE_NODES:
        if d not in stats:
            continue
        backlog, drops = queue_stats(d, "eth1")
        q_rows_b.append(f'iface_queue_backlog_bytes{{device="{d}",interface="eth1"}} {backlog}')
        q_rows_d.append(f'iface_queue_drops{{device="{d}",interface="eth1"}} {drops}')
    emit("iface_queue_backlog_bytes", "Bytes queued in the qdisc tree", "gauge", q_rows_b)
    emit("iface_queue_drops", "Cumulative qdisc drops", "counter", q_rows_d)

    # ---- REAL: routing control-plane ----------------------------------------
    rx_rows, tx_rows, rib_rows, lsa_rows = [], [], [], []
    for d in ROUTING_NODES:
        if d not in stats:
            continue
        if d.startswith("pe"):
            rx, tx = bgp_msg_counts(d)
            rx_rows.append(f'bgp_msg_rx_total{{device="{d}"}} {rx}')
            tx_rows.append(f'bgp_msg_tx_total{{device="{d}"}} {tx}')
        rib_rows.append(f'rib_routes{{device="{d}"}} {rib_routes(d)}')
        lsa_rows.append(f'ospf_lsa_count{{device="{d}"}} {ospf_lsa_count(d)}')
    emit("bgp_msg_rx_total", "BGP messages received across all peers", "counter", rx_rows)
    emit("bgp_msg_tx_total", "BGP messages sent across all peers", "counter", tx_rows)
    emit("rib_routes", "Total routes installed in the RIB", "gauge", rib_rows)
    emit("ospf_lsa_count", "OSPF link-state database entries", "gauge", lsa_rows)

    # ---- MODELLED: chassis sensors + optics ---------------------------------
    t_rows, p_rows, f_rows, v_rows = [], [], [], []
    xt_rows, xr_rows, xb_rows = [], [], []
    for d in ALL_NODES:
        if d not in stats:
            continue
        cpu_pct, _ = stats[d]
        # Chassis heat/power track FORWARDING load, not control-plane CPU: a
        # router's CPU idles even at line rate, so cpu/100 would leave these
        # features nearly flat. Use the same diurnal offered-load curve that
        # drives trafficgen, lifted by CPU when the control plane is genuinely
        # busy (reconvergence storms do add real heat).
        util = max(_diurnal_util(), min(1.0, cpu_pct / 100.0))
        role = envmodel.role_of(d)
        pop = pops.get(d) or _fallback_pop(d)
        ambient = envmodel.pop_ambient_c(pop)

        prev = float(temps.get(d, ambient))
        t = envmodel.temp_c(prev, ambient, util, 0.0, rng.gauss(0, 1))
        temps[d] = t

        t_rows.append(f'device_temp_c{{device="{d}",role="{role}",pop="pop{pop}"}} {t:.3f}')
        p_rows.append(f'device_power_watts{{device="{d}",role="{role}"}} '
                      f'{envmodel.power_watts(role, util, rng.gauss(0, 1)):.3f}')
        f_rows.append(f'device_fan_rpm{{device="{d}"}} {envmodel.fan_rpm(t):.1f}')
        v_rows.append(f'device_psu_voltage_v{{device="{d}"}} '
                      f'{envmodel.psu_voltage_v(util, rng.gauss(0, 1)):.4f}')

        # One transceiver per core-facing uplink (eth1 stands in for the optic).
        xt, xr, xb = envmodel.optical(t, age, 0.0, rng.gauss(0, 1))
        xt_rows.append(f'xcvr_temp_c{{device="{d}",interface="eth1"}} {xt:.3f}')
        xr_rows.append(f'xcvr_rx_power_dbm{{device="{d}",interface="eth1"}} {xr:.3f}')
        xb_rows.append(f'xcvr_tx_bias_ma{{device="{d}",interface="eth1"}} {xb:.3f}')

    emit("device_temp_c", "MODELLED chassis temperature (C)", "gauge", t_rows)
    emit("device_power_watts", "MODELLED chassis power draw (W)", "gauge", p_rows)
    emit("device_fan_rpm", "MODELLED fan speed (RPM)", "gauge", f_rows)
    emit("device_psu_voltage_v", "MODELLED 12V rail voltage", "gauge", v_rows)
    emit("xcvr_temp_c", "MODELLED SFF-8472 transceiver temperature (C)", "gauge", xt_rows)
    emit("xcvr_rx_power_dbm", "MODELLED SFF-8472 received optical power (dBm)", "gauge", xr_rows)
    emit("xcvr_tx_bias_ma", "MODELLED SFF-8472 laser bias current (mA)", "gauge", xb_rows)

    save_state({"temps": temps, "age_frac": age, "ts": time.time()})
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
