"""Coverage seam (#63): export.build_dataset over a fixture window yields the full
59-column schema, with the env/optical/flow feature columns NON-NULL even with
traffic generation off -- so the live feature builder gives every column the
dataset generator gives.

Prior art: test_flows_window.py (spy sources, assert + __main__, no live stack).
Run (from dataapi/):  python3 test_coverage_seam.py
"""
import export
import sources

_STEP = 30
_START = 1_700_000_010            # step-aligned (divisible by _STEP) so flow buckets
_END = _START + 2 * _STEP          # land on the same ts as the raw metric points
_BUCKETS = [_START, _START + _STEP]  # two step-aligned buckets in the window


def _series(labels, value, site_type):
    # every live series for one device carries the same site_type, else the group
    # key splits one entity across rows -- keep the fixture consistent.
    return {"metric": {"site_type": site_type, **labels},
            "values": [[b, str(value)] for b in _BUCKETS]}


# A branch CE (carries VRFs -> modelled flow) with interface/tunnel/device rows,
# PLUS a P router (no VRF -> flow stays null, the third by-design null). Values are
# arbitrary-but-present so every mapped column lands non-null. if_*_errors /
# if_*_discards are DELIBERATELY absent -- the lab's structural zeros. Each metric
# maps to a LIST of series so a device metric can carry both devices.
_DEV = "ce_branch1"    # VRF-bearing -> flow modelled
_P = "p1"              # VRF-less core -> flow null by design
_FIXTURE = {
    # interface-scoped (CE optic uplink)
    "interface_ifHCInOctets": [_series({"device": _DEV, "interface": "eth1"}, 1e6, "branch")],
    "interface_ifHCOutOctets": [_series({"device": _DEV, "interface": "eth1"}, 2e6, "branch")],
    "interface_ifOperStatus": [_series({"device": _DEV, "interface": "eth1"}, 1, "branch")],
    "iface_queue_backlog_bytes": [_series({"device": _DEV, "interface": "eth1"}, 900, "branch")],
    "iface_queue_drops": [_series({"device": _DEV, "interface": "eth1"}, 3, "branch")],
    "xcvr_temp_c": [_series({"device": _DEV, "interface": "eth1"}, 41.0, "branch")],
    "xcvr_rx_power_dbm": [_series({"device": _DEV, "interface": "eth1"}, -6.0, "branch")],
    "xcvr_tx_bias_ma": [_series({"device": _DEV, "interface": "eth1"}, 28.0, "branch")],
    # tunnel-scoped
    "sdwan_tunnel_latency_ms": [_series({"device": _DEV, "tunnel": "wg0"}, 12.0, "branch")],
    "sdwan_tunnel_jitter_ms": [_series({"device": _DEV, "tunnel": "wg0"}, 1.0, "branch")],
    "sdwan_tunnel_loss_pct": [_series({"device": _DEV, "tunnel": "wg0"}, 0.1, "branch")],
    "sdwan_tunnel_rekeys_total": [_series({"device": _DEV, "tunnel": "wg0"}, 0, "branch")],
    # device-scoped -- both the CE and the P router
    "node_cpu_pct": [_series({"device": _DEV}, 5.0, "branch"), _series({"device": _P}, 4.0, "core")],
    "node_mem_pct": [_series({"device": _DEV}, 20.0, "branch"), _series({"device": _P}, 18.0, "core")],
    "bgp_msg_rx_total": [_series({"device": _DEV}, 100, "branch"), _series({"device": _P}, 80, "core")],
    "bgp_msg_tx_total": [_series({"device": _DEV}, 90, "branch"), _series({"device": _P}, 70, "core")],
    "rib_routes": [_series({"device": _DEV}, 500, "branch"), _series({"device": _P}, 900, "core")],
    "ospf_lsa_count": [_series({"device": _DEV}, 30, "branch"), _series({"device": _P}, 60, "core")],
    "device_temp_c": [_series({"device": _DEV}, 30.0, "branch"), _series({"device": _P}, 35.0, "core")],
    "device_power_watts": [_series({"device": _DEV}, 65.0, "branch"), _series({"device": _P}, 3000.0, "core")],
    "device_fan_rpm": [_series({"device": _DEV}, 3000.0, "branch"), _series({"device": _P}, 3600.0, "core")],
    "device_psu_voltage_v": [_series({"device": _DEV}, 12.0, "branch"), _series({"device": _P}, 12.0, "core")],
}

# non-flow device features are non-null on EVERY device row; flow is checked
# separately (non-null on the VRF-bearing CE, null on the VRF-less P router).
_DEV_FEATURES = ["cpu_pct", "mem_pct", "bgp_msg_rx", "bgp_msg_tx", "rib_routes",
                 "ospf_lsa_count", "device_temp_c", "device_power_watts",
                 "device_fan_rpm", "device_psu_voltage_v"]
_IFACE_FEATURES = ["if_in_octets", "if_out_octets", "if_oper_status",
                   "q_backlog_bytes", "q_drops",
                   "xcvr_temp_c", "xcvr_rx_power_dbm", "xcvr_tx_bias_ma"]
_STRUCTURAL_ZEROS = ["if_in_errors", "if_in_discards", "if_out_errors", "if_out_discards"]


_SPIED = ("vm_query_range", "flow_rows", "label_rows", "topology_graph")


def _install_spies():
    """Patch the live sources with fixtures; return the originals to restore, so the
    stubs don't leak into other test modules run in the same process."""
    saved = {n: getattr(sources, n) for n in _SPIED}
    sources.vm_query_range = lambda metric, s, e, step: _FIXTURE.get(metric, [])
    sources.flow_rows = lambda **kw: []                  # traffic gen OFF -> modelled fallback
    sources.label_rows = lambda: [{
        "device": _DEV, "type": "gray_failure", "severity": "high",
        "t_start": export._iso(_START), "t_impact": export._iso(_END),
        "t_end": export._iso(_END + 120), "lead_time": 60, "impact_method": "modelled",
        "target": {"device": _DEV},
    }]
    sources.topology_graph = lambda: {"nodes": [{"id": _DEV, "vrfs": ["CORP", "VOICE"]},
                                                 {"id": _P, "vrfs": []}]}
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

    dev = df[df["entity_type"] == "device"]
    iface = df[df["entity_type"] == "interface"]
    assert len(dev) and len(iface)

    # 2. every non-flow device feature is non-null on EVERY device row.
    for c in _DEV_FEATURES:
        assert dev[c].notna().all(), f"device feature {c} has nulls"

    # 2b. flow: modelled + non-null on the VRF-bearing CE, null on the P router
    # (the third by-design null -- matches generate._flow_row).
    ce = dev[dev["device"] == _DEV]
    prouter = dev[dev["device"] == _P]
    assert (ce["flow_bytes"] > 0).all() and (ce["flow_packets"] > 0).all()
    assert prouter["flow_bytes"].isna().all() and prouter["flow_packets"].isna().all()

    # 3. every interface-scoped feature is non-null on interface rows.
    for c in _IFACE_FEATURES:
        assert iface[c].notna().all(), f"interface feature {c} has nulls"

    # 4. the by-design structural zeros are the ONLY absent measured feature.
    for c in _STRUCTURAL_ZEROS:
        assert df[c].isna().all(), f"{c} should be the lab's structural zero"


def test_partial_capture_gap_fills():
    # nfacctd reports ONE (device,bucket); every OTHER device/bucket must still be
    # non-null via the modelled gap-fill -- not disabled wholesale (#63 AC).
    import pandas as pd
    from datetime import datetime, timezone
    saved = _install_spies()
    real_ts = datetime.fromtimestamp(_START, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    sources.flow_rows = lambda **kw: [{"ts": real_ts, "device": _DEV,
                                       "bytes": 777.0, "packets": 5.0}]
    try:
        df = pd.read_parquet(export.build_dataset(_START, _END, _STEP))
    finally:
        _restore(saved)
    ce = df[(df["entity_type"] == "device") & (df["device"] == _DEV)].sort_values("ts")
    assert ce["flow_bytes"].notna().all(), "gap-fill left a hole"
    assert (ce["flow_bytes"] == 777.0).any(), "real capture was not preserved"
    assert (ce["flow_bytes"] != 777.0).any(), "other bucket was not modelled"


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
        # packets = bytes / 1400, each independently rounded to 0.1 -> agree within 0.1
        assert abs(a["flow_packets"].iloc[0] - round(a["flow_bytes"].iloc[0] / 1400.0, 1)) <= 0.1
        # A VRF-less P router models to nothing (null, like the real capture).
        sources.topology_graph = lambda: {"nodes": [{"id": "p1", "vrfs": []}]}
        assert export._modelled_flow(
            pd.DataFrame({"ts": [export._iso(_START)], "device": ["p1"]}), _STEP).empty
    finally:
        sources.topology_graph = saved


def _run():
    test_full_schema_and_feature_coverage()
    test_partial_capture_gap_fills()
    test_modelled_flow_deterministic()
    print("dataapi coverage-seam self-check OK")


if __name__ == "__main__":
    _run()
