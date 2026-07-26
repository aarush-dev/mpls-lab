#!/usr/bin/env python3
"""Derive the SD-WAN overlay model (hubs, spokes, tunnels, VRFs) from topology-spec.yaml.

Shared by controller.py (telemetry/path-selection) and the trafficgen, so both stay
wired to the SAME node naming the generator emits. Pure derivation from the spec —
no hardcoded node lists (matches generator/generate.py conventions).

# ponytail: minimal YAML read + index arithmetic mirroring DOCS/SPEC-NOTES.md.
#   Ceiling: if the spec's addressing formulas change, update them here too.
#   Upgrade path: have generate.py dump a model.json the controller reads instead.
"""
import os

SPEC_PATH = os.environ.get(
    "TOPO_SPEC",
    os.path.join(os.path.dirname(__file__), "..", "topology-spec.yaml"),
)


def _default_spec():
    """Fallback spec so --selftest runs with no repo checkout present."""
    return {
        "knobs": {"branch_count": 4, "hub_count": 2, "dc_count": 2},
        "vrfs": {
            "CORP": {"sites": ["branch", "hub", "dc"], "dscp_class": "AF31"},
            "VOICE": {"sites": ["branch", "hub", "dc"], "dscp_class": "EF"},
            "GUEST": {"sites": ["hub", "dc"], "dscp_class": "BE"},
        },
    }


def load_spec(path=SPEC_PATH):
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f)
    except (OSError, ImportError):
        return _default_spec()


def build_model(spec=None):
    """Return overlay model dict: hubs, spokes, tunnels, vrfs.

    tunnel = one WG path between a spoke and a hub. Each spoke peers BOTH hubs
    (per spec sdwan.spokes + SPEC-NOTES wg overlay), so tunnels = spokes x hubs.
    """
    spec = spec or load_spec()
    k = spec["knobs"]
    vrfs = spec.get("vrfs", {})

    # Hubs: ce_hub{i} -> wg 172.16.0.{i}. Only the HUB wg IP is used downstream
    # (controller._measure_rtt pings it); spoke wg IPs and WG endpoints are the
    # generator's business — a second copy here just went stale (it had the
    # pre-collision DC formula and non-routable CE loopbacks as endpoints).
    hubs = []
    for i in range(1, k["hub_count"] + 1):
        hubs.append({"node": f"ce_hub{i}", "site_type": "hub",
                     "wg_ip": f"172.16.0.{i}"})

    spokes = []
    for i in range(1, k["branch_count"] + 1):
        spokes.append({"node": f"ce_branch{i}", "site_type": "branch"})
    for i in range(1, k["dc_count"] + 1):
        spokes.append({"node": f"ce_dc{i}", "site_type": "dc"})

    # Which VRFs each site_type carries.
    site_vrfs = {}
    for vrf, vc in vrfs.items():
        for st in vc.get("sites", []):
            site_vrfs.setdefault(st, []).append(vrf)

    # Tunnels: every (spoke, hub) pair.
    tunnels = []
    for sp in spokes:
        for hub in hubs:
            tunnels.append({
                "tunnel": f"{sp['node']}-{hub['node']}",
                "site": sp["node"],
                "site_type": sp["site_type"],
                "hub": hub["node"],
                "hub_wg": hub["wg_ip"],
                "vrfs": list(site_vrfs.get(sp["site_type"], [])),
            })

    return {"hubs": hubs, "spokes": spokes, "tunnels": tunnels,
            "vrfs": vrfs, "site_vrfs": site_vrfs}


if __name__ == "__main__":
    m = build_model()
    print(f"hubs={len(m['hubs'])} spokes={len(m['spokes'])} tunnels={len(m['tunnels'])}")
    for t in m["tunnels"]:
        print(t["tunnel"], t["vrfs"])
