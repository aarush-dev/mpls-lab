#!/usr/bin/env python3
"""G10 realism-gap discriminator.

Trains a gradient-boosted tree to tell REAL live-lab rows (label 0) from
SYNTHETIC rows (label 1) using ONLY the ~40 downstream feature columns -- no
labels/ids/provenance, which would leak trivially. If the tree can't separate
them (AUC ~0.5) the synthetic distribution transfers; if it can (AUC >0.95) it
won't. Top permutation importances name exactly which features to fix.

Run: python3 synthetic/discriminator.py
"""
import glob
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.model_selection import cross_val_score, train_test_split

# The 28 numeric feature columns the downstream model sees. Real & synthetic
# differ in NaN pattern (each row fills only its entity_type's pillars) -- HistGBT
# eats NaN natively, so we do NOT impute: divergent NaN structure is itself a
# realism signal, not something to hide.
NUMERIC = [
    "if_in_octets", "if_out_octets", "if_oper_status", "tunnel_latency_ms",
    "tunnel_jitter_ms", "tunnel_loss_pct", "tunnel_rekeys", "flow_bytes",
    "flow_packets", "if_in_errors", "if_in_discards", "if_out_errors",
    "if_out_discards", "q_backlog_bytes", "q_drops", "xcvr_temp_c",
    "xcvr_rx_power_dbm", "xcvr_tx_bias_ma", "cpu_pct", "mem_pct", "bgp_msg_rx",
    "bgp_msg_tx", "rib_routes", "ospf_lsa_count", "device_temp_c",
    "device_power_watts", "device_fan_rpm", "device_psu_voltage_v",
]
# Structural categoricals -- fair game, one-hot encoded.
CATEG = ["entity_type", "site_type"]
REAL_GLOB = "/root/LAB/dataapi/datasets/*.parquet"
SYNTH = "/root/LAB/synthetic/output/synthetic_multi_d0.05_s30_x3.0_n8.parquet"
SEED = 30


def load():
    real = pd.concat(pd.read_parquet(f) for f in glob.glob(REAL_GLOB))
    cols = NUMERIC + CATEG + ["topology_id", "stream"]
    synth = pd.read_parquet(SYNTH, columns=cols)
    # Compare like-for-like: single base topology, stream F (drop F if it
    # starves; N is same-shape). Real lab == one base topology already.
    base = synth[synth.topology_id == "topo_p6_pe12_r2_base"]
    fs = base[base.stream == "F"]
    synth = fs if len(fs) >= 5000 else base[base.stream.isin(["F", "N"])]
    # Balance: downsample the larger class to match the smaller.
    n = min(len(real), len(synth))
    real = real.sample(n, random_state=SEED)
    synth = synth.sample(n, random_state=SEED)
    df = pd.concat([real.assign(_y=0), synth.assign(_y=1)], ignore_index=True)
    X = pd.get_dummies(df[NUMERIC + CATEG], columns=CATEG, dummy_na=False)
    return X, df["_y"].to_numpy(), n


def main():
    X, y, n = load()
    print(f"balanced: {n} real vs {n} synthetic ({X.shape[1]} features)")
    clf = HistGradientBoostingClassifier(random_state=SEED)

    auc = cross_val_score(clf, X, y, cv=5, scoring="roc_auc", n_jobs=-1)
    print(f"AUC (5-fold): {auc.mean():.4f} +/- {auc.std():.4f}")

    # HistGBT has no native importances -> permutation on a held-out split.
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25,
                                          random_state=SEED, stratify=y)
    clf.fit(Xtr, ytr)
    imp = permutation_importance(clf, Xte, yte, scoring="roc_auc",
                                 n_repeats=5, random_state=SEED, n_jobs=-1)
    order = np.argsort(imp.importances_mean)[::-1][:5]
    print("top-5 permutation importances (AUC drop when shuffled):")
    for i in order:
        print(f"  {X.columns[i]:<24} {imp.importances_mean[i]:+.4f}")


if __name__ == "__main__":
    main()
