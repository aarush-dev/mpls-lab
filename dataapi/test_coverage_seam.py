"""Coverage seam (#63): export.build_dataset over a fixture window yields the full
59-column schema, with the env/optical/flow feature columns NON-NULL even with
traffic generation off -- so the live feature builder gives every column the
dataset generator gives.

Prior art: test_flows_window.py (spy sources, assert + __main__, no live stack).
Run (from dataapi/):  python3 test_coverage_seam.py
"""
import numpy as np

import export
import sources

_START, _END, _STEP = 1_700_000_000, 1_700_000_060, 30
_BUCKETS = [_START, _START + _STEP]  # two step-aligned buckets in the window


def _series(labels, value):
    # every live series for one device carries the same site_type, else the group
    # key splits one entity across rows -- keep the fixture consistent.
    return {"metric": {"site_type": "branch", **labels},
            "values": [[b, str(value)] for b in _BUCKETS]}


# One device (a branch CE, so it carries VRFs -> modelled flow) with an interface,
# a tunnel and a device row. Values are arbitrary-but-present so every mapped
# column lands non-null. if_*_errors / if_*_discards are DELIBERATELY absent --
# the lab's by-design structural zeros.
_DEV = "ce_branch1"
_FIXTURE = {
    # interface-scoped
    "interface_ifHCInOctets": _series({"device": _DEV, "interface": "eth1", "site_type": "branch"}, 1e6),
    "interface_ifHCOutOctets": _series({"device": _DEV, "interface": "eth1", "site_type": "branch"}, 2e6),
    "interface_ifOperStatus": _series({"device": _DEV, "interface": "eth1", "site_type": "branch"}, 1),
    "iface_queue_backlog_bytes": _series({"device": _DEV, "interface": "eth1"}, 900),
    "iface_queue_drops": _series({"device": _DEV, "interface": "eth1"}, 3),
    "xcvr_temp_c": _series({"device": _DEV, "interface": "eth1"}, 41.0),
    "xcvr_rx_power_dbm": _series({"device": _DEV, "interface": "eth1"}, -6.0),
    "xcvr_tx_bias_ma": _series({"device": _DEV, "interface": "eth1"}, 28.0),
    # tunnel-scoped
    "sdwan_tunnel_latency_ms": _series({"device": _DEV, "tunnel": "wg0"}, 12.0),
    "sdwan_tunnel_jitter_ms": _series({"device": _DEV, "tunnel": "wg0"}, 1.0),
    "sdwan_tunnel_loss_pct": _series({"device": _DEV, "tunnel": "wg0"}, 0.1),
    "sdwan_tunnel_rekeys_total": _series({"device": _DEV, "tunnel": "wg0"}, 0),
    # device-scoped
    "node_cpu_pct": _series({"device": _DEV}, 5.0),
    "node_mem_pct": _series({"device": _DEV}, 20.0),
    "bgp_msg_rx_total": _series({"device": _DEV}, 100),
    "bgp_msg_tx_total": _series({"device": _DEV}, 90),
    "rib_routes": _series({"device": _DEV}, 500),
    "ospf_lsa_count": _series({"device": _DEV}, 30),
    "device_temp_c": _series({"device": _DEV}, 30.0),
    "device_power_watts": _series({"device": _DEV}, 65.0),
    "device_fan_rpm": _series({"device": _DEV}, 3000.0),
    "device_psu_voltage_v": _series({"device": _DEV}, 12.0),
}

_DEV_FEATURES = ["cpu_pct", "mem_pct", "bgp_msg_rx", "bgp_msg_tx", "rib_routes",
                 "ospf_lsa_count", "device_temp_c", "device_power_watts",
                 "device_fan_rpm", "device_psu_voltage_v",
                 "flow_bytes", "flow_packets"]           # <-- the #63 flow columns
_IFACE_FEATURES = ["if_in_octets", "if_out_octets", "if_oper_status",
                   "q_backlog_bytes", "q_drops",
                   "xcvr_temp_c", "xcvr_rx_power_dbm", "xcvr_tx_bias_ma"]
_STRUCTURAL_ZEROS = ["if_in_errors", "if_in_discards", "if_out_errors", "if_out_discards"]


_SPIED = ("vm_query_range", "flow_rows", "label_rows", "topology_graph")


def _install_spies():
    """Patch the live sources with fixtures; return the originals to restore, so the
    stubs don't leak into other test modules run in the same process."""
    saved = {n: getattr(sources, n) for n in _SPIED}
    sources.vm_query_range = lambda metric, s, e, step: (_FIXTURE.get(metric) and [_FIXTURE[metric]]) or []
    sources.flow_rows = lambda **kw: []                  # traffic gen OFF -> modelled fallback
    sources.label_rows = lambda: [{
        "device": _DEV, "type": "gray_failure", "severity": "high",
        "t_start": export._iso(_START), "t_impact": export._iso(_END),
        "t_end": export._iso(_END + 120), "lead_time": 60, "impact_method": "modelled",
        "target": {"device": _DEV},
    }]
    sources.topology_graph = lambda: {"nodes": [{"id": _DEV, "vrfs": ["CORP", "VOICE"]}]}
    return saved


def _restore(saved):
    for n, fn in saved.items():
        setattr(sources, n, fn)


def test_full_schema_and_feature_coverage():
    saved = _install_spies()
    try:
        path = export.build_dataset(_START, _END, _STEP)
    finally:
        _restore(saved)
    import pandas as pd
    df = pd.read_parquet(path)

    # 1. full canonical schema, exact column set (matches a dataset parquet).
    assert list(df.columns) == export.COLUMNS, set(df.columns) ^ set(export.COLUMNS)
    assert len(df.columns) == 59, len(df.columns)

    dev = df[df["entity_type"] == "device"]
    iface = df[df["entity_type"] == "interface"]
    assert len(dev) and len(iface)

    # 2. every device-scoped feature (incl. the two flow columns) is non-null.
    for c in _DEV_FEATURES:
        assert dev[c].notna().all(), f"device feature {c} has nulls"
    assert (dev["flow_bytes"] > 0).all() and (dev["flow_packets"] > 0).all()

    # 3. every interface-scoped feature is non-null on interface rows.
    for c in _IFACE_FEATURES:
        assert iface[c].notna().all(), f"interface feature {c} has nulls"

    # 4. the by-design structural zeros are the ONLY absent measured feature.
    for c in _STRUCTURAL_ZEROS:
        assert df[c].isna().all(), f"{c} should be the lab's structural zero"


def test_modelled_flow_deterministic():
    # No RNG: same (device, bucket) -> same bytes; packets = bytes / 1400.
    import pandas as pd
    saved = sources.topology_graph
    try:
        sources.topology_graph = lambda: {"nodes": [{"id": _DEV, "vrfs": ["CORP", "VOICE"]}]}
        rows = pd.DataFrame({"ts": [export._iso(_START)], "device": [_DEV]})
        a = export._modelled_flow(rows, _STEP)
        b = export._modelled_flow(rows, _STEP)
        assert a["flow_bytes"].iloc[0] == b["flow_bytes"].iloc[0] > 0
        assert np.isclose(a["flow_bytes"].iloc[0] / a["flow_packets"].iloc[0], 1400.0, rtol=1e-4)
        # A VRF-less P router models to nothing (null, like the real capture).
        sources.topology_graph = lambda: {"nodes": [{"id": "p1", "vrfs": []}]}
        assert export._modelled_flow(
            pd.DataFrame({"ts": [export._iso(_START)], "device": ["p1"]}), _STEP).empty
    finally:
        sources.topology_graph = saved


def _run():
    test_full_schema_and_feature_coverage()
    test_modelled_flow_deterministic()
    print("dataapi coverage-seam self-check OK")


if __name__ == "__main__":
    _run()
