#!/usr/bin/env python3
"""Fault-injection primitives for the SD-WAN-over-MPLS Containerlab lab.

Each injector targets a LIVE node via `docker exec` (or the native
`containerlab tools netem` CLI) and exposes:
    apply()   -> mutate the lab (returns a short dict describing what was done)
    revert()  -> undo it, returning the node to baseline (idempotent)

Design (caveman+ponytail): reuse native tools, don't reinvent tc/netem/ip/vtysh.
stdlib only. Everything is recoverable; revert() is safe to call twice.

# ponytail: node names are `clab-<lab>-<device>`. We never parse interface
#   indexes — callers pass the interface explicitly (the topology is stable).

KEY NETEM DETAIL (discovered against the live lab):
  - P/PE core interfaces have a `noqueue` root qdisc -> `containerlab tools
    netem set` works directly (it installs a root netem qdisc).
  - CE *uplinks* (eth1) already carry an HTB QoS root (3 classes). A root netem
    install fails ("Invalid qdisc name: must match existing qdisc"). So on CE
    uplinks we attach netem as a LEAF qdisc under the HTB default class (1:30),
    replacing its fq_codel leaf. This (a) preserves QoS, (b) is still visible to
    the controller, which greps `tc qdisc show dev eth1` for delay/loss tokens
    and folds them into the emitted tunnel telemetry. revert() restores fq_codel.
"""
import subprocess
import time

LAB = "sdwan_mpls_noc"


def node(device):
    """Map a topology device name (e.g. 'pe1', 'ce_branch1') to its container."""
    return f"clab-{LAB}-{device}"


def _sh(args, timeout=15, check=False):
    """Run a command, return CompletedProcess. Never raises unless check=True."""
    return subprocess.run(args, capture_output=True, text=True,
                          timeout=timeout, check=check)


def dexec(device, *cmd, timeout=15):
    """docker exec into a node and run cmd. Returns CompletedProcess."""
    return _sh(["docker", "exec", node(device), *cmd], timeout=timeout)


# ---------------------------------------------------------------------------
# 1. Link impairment (netem) — delay / jitter / loss / rate, with RAMP support.
# ---------------------------------------------------------------------------
class NetemImpair:
    """Apply netem impairment to <device>:<iface>.

    On an interface whose root is HTB (CE uplink) we splice netem under the HTB
    default class; otherwise we use the native `containerlab tools netem set`.

    ramp(): step the impairment up over `steps` increments to simulate a
    congestion *buildup* (the precursor the ML models learn from).
    """

    HTB_DEFAULT_PARENT = "1:30"   # HTB default class on CE uplinks (default 0x30)
    HTB_LEAF_HANDLE = "31:"       # our netem leaf handle
    HTB_BASELINE_LEAF = "30:"     # fq_codel handle to restore on revert

    def __init__(self, device, iface, delay_ms=0.0, jitter_ms=0.0,
                 loss_pct=0.0, rate_kbit=0):
        self.device = device
        self.iface = iface
        self.delay_ms = delay_ms
        self.jitter_ms = jitter_ms
        self.loss_pct = loss_pct
        self.rate_kbit = rate_kbit
        self._is_htb = self._root_is_htb()

    def _root_is_htb(self):
        out = dexec(self.device, "tc", "qdisc", "show", "dev", self.iface).stdout
        return "qdisc htb 1:" in out

    def _netem_args(self, delay_ms, jitter_ms, loss_pct, rate_kbit):
        a = []
        if delay_ms > 0:
            a += ["delay", f"{delay_ms}ms"]
            if jitter_ms > 0:
                a += [f"{jitter_ms}ms"]
        if loss_pct > 0:
            a += ["loss", f"{loss_pct}%"]
        if rate_kbit > 0:
            a += ["rate", f"{rate_kbit}kbit"]
        return a

    def _set(self, delay_ms, jitter_ms, loss_pct, rate_kbit):
        """Install/replace the netem impairment with the given parameters."""
        if self._is_htb:
            # Splice netem as the HTB default-class leaf (replace fq_codel).
            args = ["tc", "qdisc", "replace", "dev", self.iface,
                    "parent", self.HTB_DEFAULT_PARENT, "handle", self.HTB_LEAF_HANDLE,
                    "netem"] + self._netem_args(delay_ms, jitter_ms, loss_pct, rate_kbit)
            return dexec(self.device, *args)
        # Native containerlab CLI for noqueue-root interfaces (P/PE core links).
        args = ["containerlab", "tools", "netem", "set",
                "-n", node(self.device), "-i", self.iface]
        if delay_ms > 0:
            args += ["--delay", f"{delay_ms}ms"]
        if jitter_ms > 0:
            args += ["--jitter", f"{jitter_ms}ms"]
        if loss_pct > 0:
            args += ["--loss", str(loss_pct)]
        if rate_kbit > 0:
            args += ["--rate", str(rate_kbit)]
        return _sh(args)

    def apply(self):
        self._set(self.delay_ms, self.jitter_ms, self.loss_pct, self.rate_kbit)
        return {"injector": "netem", "mode": "htb_leaf" if self._is_htb else "clab_netem",
                "device": self.device, "iface": self.iface,
                "delay_ms": self.delay_ms, "jitter_ms": self.jitter_ms,
                "loss_pct": self.loss_pct, "rate_kbit": self.rate_kbit}

    def ramp(self, steps=6, step_seconds=10.0, on_step=None):
        """Gradually increase impairment from 0 to target over `steps`.

        Simulates congestion building up (queue fills, then loss starts). Calls
        on_step(i, frac) after each step if provided (lets the orchestrator poll
        telemetry mid-ramp). Returns the apply() descriptor of the final step.
        """
        desc = None
        for i in range(1, steps + 1):
            frac = i / steps
            self._set(self.delay_ms * frac, self.jitter_ms * frac,
                      self.loss_pct * frac, 0)  # don't ramp rate (binary cap)
            desc = {"injector": "netem", "mode": "htb_leaf" if self._is_htb else "clab_netem",
                    "device": self.device, "iface": self.iface, "ramp_step": i,
                    "ramp_steps": steps, "delay_ms": round(self.delay_ms * frac, 2),
                    "loss_pct": round(self.loss_pct * frac, 2)}
            if on_step:
                on_step(i, frac)
            if i < steps:
                time.sleep(step_seconds)
        # apply final rate cap if any
        if self.rate_kbit > 0:
            self._set(self.delay_ms, self.jitter_ms, self.loss_pct, self.rate_kbit)
        return desc

    def revert(self):
        if self._is_htb:
            # Restore fq_codel leaf under the HTB default class.
            dexec(self.device, "tc", "qdisc", "replace", "dev", self.iface,
                  "parent", self.HTB_DEFAULT_PARENT, "handle", self.HTB_BASELINE_LEAF,
                  "fq_codel")
        else:
            _sh(["containerlab", "tools", "netem", "reset",
                 "-n", node(self.device), "-i", self.iface])
            # Belt-and-suspenders: ensure no netem root lingers.
            dexec(self.device, "tc", "qdisc", "del", "dev", self.iface, "root")
        return {"reverted": "netem", "device": self.device, "iface": self.iface}


# ---------------------------------------------------------------------------
# 2. Link flap — ip link down/up.
# ---------------------------------------------------------------------------
class LinkFlap:
    """Bring an interface down, hold, bring it up. Models a flapping link /
    adjacency loss. revert() guarantees the link is back up."""

    def __init__(self, device, iface, down_seconds=10.0, count=1):
        self.device = device
        self.iface = iface
        self.down_seconds = down_seconds
        self.count = count

    def apply(self):
        for _ in range(self.count):
            dexec(self.device, "ip", "link", "set", self.iface, "down")
            time.sleep(self.down_seconds)
            dexec(self.device, "ip", "link", "set", self.iface, "up")
            if self.count > 1:
                time.sleep(2.0)  # brief up between flaps
        return {"injector": "link_flap", "device": self.device,
                "iface": self.iface, "count": self.count,
                "down_seconds": self.down_seconds}

    def revert(self):
        dexec(self.device, "ip", "link", "set", self.iface, "up")
        return {"reverted": "link_flap", "device": self.device, "iface": self.iface}


# ---------------------------------------------------------------------------
# 3. BGP flap — vtysh clear (session reset). Adjacency churn precursor.
# ---------------------------------------------------------------------------
class BgpFlap:
    """Reset BGP sessions via vtysh `clear bgp`. neighbor=None clears all;
    otherwise clears a specific neighbor IP.

    vrf=<name>  : target ONE specific VRF (original param kept for callers)
    vrf=None    : DEFAULT — enumerate ALL BGP instances on the node (default
                  instance + every VRF) and clear each. This is the only way
                  to hit CE routers, which run BGP exclusively inside VRFs
                  (vrf_CORP / vrf_VOICE / vrf_GUEST); `clear bgp *` against
                  the default instance is a no-op on them.

    # ponytail: discover VRFs dynamically via `show bgp vrf all summary`
    #   (one exec, already needed for PolicyDrift) rather than hardcoding names;
    #   we ignore errors for absent instances so PE/P (default only) just work.

    This is non-destructive (sessions re-establish) but produces ADJCHANGE
    syslog (-> Loki) and a routing churn signature."""

    def __init__(self, device, neighbor=None, vrf=None, count=1, gap_seconds=8.0):
        self.device = device
        self.neighbor = neighbor
        self.vrf = vrf      # None = all instances; str = one VRF only
        self.count = count
        self.gap_seconds = gap_seconds

    def _discover_vrfs(self):
        """Return list of VRF names present on the node (empty = default only)."""
        out = dexec(self.device, "vtysh", "-c", "show bgp vrf all summary").stdout
        vrfs = []
        for ln in out.splitlines():
            # Line: "BGP router identifier ... local AS number <n> VRF <name> vrf-id ..."
            if "local AS number" in ln and " VRF " in ln:
                vrf = ln.split(" VRF ")[1].split()[0]
                vrfs.append(vrf)
        return vrfs

    def _clear_cmds(self):
        """Return list of vtysh clear commands to issue."""
        tgt = self.neighbor or "*"
        if self.vrf:
            # Caller pinned one VRF explicitly.
            return [f"clear bgp vrf {self.vrf} {tgt}"]
        # Enumerate: try the default instance first, then every VRF found.
        cmds = [f"clear bgp {tgt}"]   # no-op on CE (default absent), harmless
        for vrf in self._discover_vrfs():
            cmds.append(f"clear bgp vrf {vrf} {tgt}")
        return cmds

    def apply(self):
        cmds = self._clear_cmds()
        for i in range(self.count):
            for cmd in cmds:
                dexec(self.device, "vtysh", "-c", cmd)  # ignore rc; absent instance returns error but doesn't abort
            if i < self.count - 1:
                time.sleep(self.gap_seconds)
        return {"injector": "bgp_flap", "device": self.device,
                "neighbor": self.neighbor, "vrf": self.vrf,
                "count": self.count, "cmds": cmds}

    def revert(self):
        # BGP re-converges on its own; nothing to undo. Touch to confirm liveness.
        return {"reverted": "bgp_flap", "device": self.device,
                "note": "sessions self-recover (clear is transient)"}


# ---------------------------------------------------------------------------
# 4. Process kill — kill -9 bgpd; watchfrr restarts it. Recoverable.
# ---------------------------------------------------------------------------
class ProcessKill:
    """kill -9 a routing daemon (default bgpd). FRR's watchfrr respawns it.
    revert() verifies the process is back (and nudges a restart if not)."""

    def __init__(self, device, proc="bgpd"):
        self.device = device
        self.proc = proc

    def _pid(self):
        out = dexec(self.device, "pidof", self.proc).stdout.strip()
        return out.split()[0] if out else None

    def apply(self):
        pid = self._pid()
        if pid:
            dexec(self.device, "kill", "-9", pid)
        return {"injector": "process_kill", "device": self.device,
                "proc": self.proc, "killed_pid": pid}

    def revert(self):
        # Give watchfrr a chance, then force-restart if still dead.
        for _ in range(12):
            if self._pid():
                return {"reverted": "process_kill", "device": self.device,
                        "proc": self.proc, "note": "watchfrr respawned"}
            time.sleep(5.0)
        # Last resort: ask watchfrr/frr to restart the daemon.
        dexec(self.device, "/usr/lib/frr/watchfrr.sh", "restart", self.proc, timeout=30)
        return {"reverted": "process_kill", "device": self.device,
                "proc": self.proc, "note": "forced restart"}


# ---------------------------------------------------------------------------
# 5. WireGuard rekey anomaly — bounce wg0 to force a fresh handshake storm.
# ---------------------------------------------------------------------------
class WgRekeyAnomaly:
    """Force WireGuard handshake churn on a spoke by toggling wg0 (and
    optionally re-applying the running config). The controller models rekey
    clustering under loss; pairing this with netem yields the rekey-anomaly
    signature. revert() ensures wg0 is up."""

    def __init__(self, device, iface="wg0", count=3, gap_seconds=4.0):
        self.device = device
        self.iface = iface
        self.count = count
        self.gap_seconds = gap_seconds

    def apply(self):
        for i in range(self.count):
            dexec(self.device, "ip", "link", "set", self.iface, "down")
            time.sleep(1.0)
            dexec(self.device, "ip", "link", "set", self.iface, "up")
            if i < self.count - 1:
                time.sleep(self.gap_seconds)
        return {"injector": "wg_rekey", "device": self.device,
                "iface": self.iface, "count": self.count}

    def revert(self):
        dexec(self.device, "ip", "link", "set", self.iface, "up")
        return {"reverted": "wg_rekey", "device": self.device, "iface": self.iface}


# ---------------------------------------------------------------------------
# 6. Policy / route drift — perturb a CE per-VRF BGP local-preference via a
#    route-map (real, observable policy drift; no controller source edits).
# ---------------------------------------------------------------------------
class PolicyDrift:
    """Inject a route-map on a CE VRF bgpd that lowers local-preference on
    inbound routes from the PE, drifting path selection away from policy.
    Observable in `show bgp` and as a BGP soft-clear ADJCHANGE in Loki.

    revert() removes the route-map and soft-clears to restore the baseline.
    """

    RMAP = "FAULT_DRIFT"

    def __init__(self, device, vrf="vrf_CORP", neighbor=None, local_pref=50):
        self.device = device
        self.vrf = vrf
        self.neighbor = neighbor          # PE peer IP; auto-detected if None
        self.local_pref = local_pref

    def _detect_neighbor(self):
        if self.neighbor:
            return self.neighbor
        out = dexec(self.device, "vtysh", "-c",
                    f"show bgp vrf {self.vrf} summary").stdout
        for ln in out.splitlines():
            ln = ln.strip()
            if ln[:2].isdigit() and "." in ln.split()[0]:
                return ln.split()[0]
        return None

    def _as_number(self):
        out = dexec(self.device, "vtysh", "-c",
                    f"show bgp vrf {self.vrf} summary").stdout
        for ln in out.splitlines():
            if "local AS number" in ln:
                return ln.split("local AS number")[1].split()[0]
        return None

    def apply(self):
        nb = self._detect_neighbor()
        asn = self._as_number()
        cfg = "\n".join([
            "configure terminal",
            f"route-map {self.RMAP} permit 10",
            f" set local-preference {self.local_pref}",
            "exit",
            f"router bgp {asn} vrf {self.vrf}",
            " address-family ipv4 unicast",
            f"  neighbor {nb} route-map {self.RMAP} in",
            " exit-address-family",
            "exit",
            "exit",
        ])
        dexec(self.device, "vtysh", "-c", cfg)
        dexec(self.device, "vtysh", "-c",
              f"clear bgp vrf {self.vrf} {nb} soft in")
        return {"injector": "policy_drift", "device": self.device,
                "vrf": self.vrf, "neighbor": nb, "asn": asn,
                "local_pref": self.local_pref, "route_map": self.RMAP}

    def revert(self):
        nb = self._detect_neighbor()
        asn = self._as_number()
        cfg = "\n".join([
            "configure terminal",
            f"router bgp {asn} vrf {self.vrf}",
            " address-family ipv4 unicast",
            f"  no neighbor {nb} route-map {self.RMAP} in",
            " exit-address-family",
            "exit",
            f"no route-map {self.RMAP}",
            "exit",
        ])
        dexec(self.device, "vtysh", "-c", cfg)
        dexec(self.device, "vtysh", "-c",
              f"clear bgp vrf {self.vrf} {nb} soft in")
        return {"reverted": "policy_drift", "device": self.device, "vrf": self.vrf}
