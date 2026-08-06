"""PA-A4: the model's channel order + metadata-embedding code maps.

`CHANNELS` is the authoritative 28-channel order the PA model expects
(FEATURE_COLUMNS in the model's `src/data/schema.py`). The live window builder
(PA-A5) selects `df[CHANNELS]` so the array columns line up with the weights.
It is ALSO fetchable at runtime from `/v1/health.channels`; this copy lets the
builder + tests run without the service and is asserted equal to health in PA-A8.

`entity_type_code` / `site_type_code` feed the encoder's metadata embeddings. The
training vocab (built first-seen during `prepare`, prepare.py:77) was NOT shipped
with the run, so exact codes are unrecoverable. We map the known lab categories to
stable small ints and fall back to 0 for anything unseen. This is a MINOR signal
(an embedding row); a wrong code slightly perturbs one input, it cannot break
inference. Upgrade path: if the training vocab is exported, replace these maps.
ponytail: best-effort map, 0-fallback -- documented approximation, not a guess
dressed as truth.
"""
from __future__ import annotations

# 28 channels, model order (FEATURE_COLUMNS). Keep in lockstep with /v1/health.
CHANNELS: list[str] = [
    "if_in_octets", "if_out_octets", "if_oper_status",
    "if_in_errors", "if_in_discards", "if_out_errors", "if_out_discards",
    "tunnel_latency_ms", "tunnel_jitter_ms", "tunnel_loss_pct", "tunnel_rekeys",
    "q_backlog_bytes", "q_drops",
    "bgp_msg_rx", "bgp_msg_tx", "rib_routes", "ospf_lsa_count",
    "flow_bytes", "flow_packets",
    "cpu_pct", "mem_pct",
    "device_temp_c", "device_power_watts", "device_fan_rpm", "device_psu_voltage_v",
    "xcvr_temp_c", "xcvr_rx_power_dbm", "xcvr_tx_bias_ma",
]
assert len(CHANNELS) == 28

# Approximate metadata codes (0-fallback). Order = the three entity types + the
# lab's site types; exact training codes need the (unshipped) vocab.
_ENTITY_TYPE_CODE = {"interface": 0, "tunnel": 1, "device": 2}
_SITE_TYPE_CODE = {"core": 0, "pe": 1, "branch": 2, "hub": 3, "dc": 4}


def entity_type_code(entity_type: str) -> int:
    return _ENTITY_TYPE_CODE.get(str(entity_type), 0)


def site_type_code(site_type) -> int:
    return _SITE_TYPE_CODE.get(str(site_type), 0)


if __name__ == "__main__":
    print(f"CHANNELS={len(CHANNELS)}  entity_types={_ENTITY_TYPE_CODE}  site_types={_SITE_TYPE_CODE}")
