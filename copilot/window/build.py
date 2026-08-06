"""PA-A5: live telemetry -> the model's L×C windows, one per entity.

The real PA `/v1/predict[/snapshot]` scores a window of shape (L=168, C=28) per
entity, channels in the model's FEATURE_COLUMNS order (from `/v1/health.channels`).
Nothing in the pipeline assembled that from live telemetry -- this does, reusing
`dataapi.export.export_df` (the same VM->schema join the training corpus was built
from, so channel names/units/cadence already match by construction).

Per entity `(device, entity, entity_type)` over the last (L-1)*step seconds:
  - `export_df(now-(L-1)*step, now, step)` -> the long labeled table,
  - reindex to exactly L step-aligned timestamps ending at `now`,
  - select the model's channels -> (L, C); a channel not applicable to the entity
    (bgp_* on an interface row, tunnel_* on a device row) stays NaN,
  - emit NaN as JSON `null`: the model builds its observed-mask from `isfinite`.

`export_df` is injected in tests (the live VM/dataapi need not be up).

Self-check:  python3 -m copilot.window.build
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd


def _epoch(now_iso: str) -> int:
    d = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def _export_df(start: int, end: int, step: int):
    """Default live source: dataapi's export_df. Imported lazily so a test that
    injects `export_df=` never needs dataapi/VM importable. The dataapi dir is put
    on sys.path first: export.py imports its sibling `sources` by bare name."""
    import importlib
    import os
    import sys
    dataapi_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "dataapi")
    if dataapi_dir not in sys.path:
        sys.path.insert(0, dataapi_dir)
    return importlib.import_module("dataapi.export").export_df(start, end, step)


def build_windows(now_iso: str, channels: list[str], *, L: int = 168, step: int = 30,
                  etc_of=None, stc_of=None, export_df=None) -> dict[str, dict]:
    """Return {entity: {window, entity_type, vrf, device, etc, stc}} for `now_iso`.

    `channels` = the model's channel order (`/v1/health.channels`). `etc_of`/`stc_of`
    map entity_type/site_type -> the model's metadata embedding codes (PA-A4);
    default 0 when absent. `export_df` overrides the live source for tests."""
    end = _epoch(now_iso)
    start = end - (L - 1) * step
    df = (export_df or _export_df)(start, end, step)
    if df is None or len(df) == 0:
        return {}
    # step-aligned grid of exactly L timestamps ending at `now` (UTC, us to match ts).
    grid = pd.date_range(end=pd.Timestamp(end, unit="s", tz="UTC"), periods=L,
                         freq=f"{step}s")
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    out: dict[str, dict] = {}
    for (device, entity, etype), g in df.groupby(["device", "entity", "entity_type"]):
        g = g.set_index("ts").sort_index()
        g = g[~g.index.duplicated(keep="last")]      # one row per bucket before reindex
        g = g.reindex(grid)
        # missing channels aren't in a device row's frame at all -> add as NaN col
        mat = np.full((L, len(channels)), np.nan, dtype=np.float32)
        for j, ch in enumerate(channels):
            if ch in g.columns:
                mat[:, j] = pd.to_numeric(g[ch], errors="coerce").to_numpy(dtype=np.float32)
        site_type = g["site_type"].dropna().iloc[-1] if "site_type" in g and g["site_type"].notna().any() else None
        vrf = g["vrf"].dropna().iloc[-1] if "vrf" in g and g["vrf"].notna().any() else ""
        out[str(entity)] = {
            "window": [[None if not np.isfinite(v) else float(v) for v in row] for row in mat],
            "entity_type": str(etype), "vrf": vrf if isinstance(vrf, str) else "",
            "device": str(device),
            "etc": int(etc_of(str(etype)) if etc_of else 0),
            "stc": int(stc_of(site_type) if stc_of and site_type is not None else 0),
        }
    return out


def _selfcheck():
    """Fabricate a 2-entity export_df; assert each window is (L,28), channel order
    honoured, absent channels -> null, N entities returned."""
    from copilot.window.vocab import CHANNELS  # 28 channels in model order

    L, step = 168, 30
    end = 1_754_480_000
    ts = pd.date_range(end=pd.Timestamp(end, unit="s", tz="UTC"), periods=L, freq=f"{step}s")

    def fake_export_df(start, end_, step_):
        rows = []
        # a device row (has bgp/cpu, no tunnel) and an interface row (has if_*, no bgp)
        for t in ts:
            rows.append({"ts": t, "device": "pe1", "entity": "pe1", "entity_type": "device",
                         "site_type": "pe", "vrf": None, "cpu_pct": 12.0, "bgp_msg_rx": 5.0,
                         "if_in_octets": np.nan})
            rows.append({"ts": t, "device": "pe1", "entity": "pe1:eth1", "entity_type": "interface",
                         "site_type": "pe", "vrf": "CORP", "if_in_octets": 1e6, "cpu_pct": np.nan})
        return pd.DataFrame(rows)

    now_iso = datetime.fromtimestamp(end, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    w = build_windows(now_iso, CHANNELS, L=L, step=step, export_df=fake_export_df)
    assert set(w) == {"pe1", "pe1:eth1"}, w.keys()
    for e, d in w.items():
        assert len(d["window"]) == L and len(d["window"][0]) == len(CHANNELS), e
    ci = CHANNELS.index("cpu_pct")
    bi = CHANNELS.index("if_in_octets")
    # device row: cpu present, if_in_octets absent(null); interface row: opposite
    assert w["pe1"]["window"][-1][ci] == 12.0 and w["pe1"]["window"][-1][bi] is None
    assert w["pe1:eth1"]["window"][-1][bi] == 1e6 and w["pe1:eth1"]["window"][-1][ci] is None
    print(f"OK build_windows: {len(w)} entities, (L,C)=({L},{len(CHANNELS)}), "
          f"channel order + null-masking verified")


if __name__ == "__main__":
    _selfcheck()
