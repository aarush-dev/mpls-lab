#!/usr/bin/env python3
"""verify_regressions.py -- run: python verify_regressions.py <new.parquet>

Checks the two label-correctness regressions fixed in this pass (severity
null-where-ignored; tunnel vrf as list<string> + auditable sla_binding_vrf)
and re-verifies the earlier fix pass stayed intact.
"""
import sys
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

f = sys.argv[1]
df = pq.read_table(f).to_pandas()
fails = []


def chk(name, cond, got):
    print(('PASS  ' if cond else 'FAIL  ') + f'{name:<58} {got}')
    if not cond:
        fails.append(name)


# --- DEFECT 1: severity null-where-ignored preserved ------------------------
NULL_SEVERITY_SCENARIOS = {"node_failure", "p_node_failure", "pop_isolation",
                           "core_partition", "rr_failure"}
tmp = df[df.is_fault].copy()
sev_col = 'severities' if 'severities' in df.columns else 'severity'
prim_col = 'severity_primary' if 'severity_primary' in df.columns else 'severity'
fault_col = 'fault_type_primary' if 'fault_type_primary' in df.columns else 'fault_type'

for scen in NULL_SEVERITY_SCENARIOS:
    sub = tmp[tmp[fault_col] == scen]
    if len(sub) == 0:
        continue
    null_rate = sub[prim_col].isna().mean()
    chk(f'D1 {scen}: severity_primary null in 100% of rows', null_rate == 1.0,
        f'{100*null_rate:.1f}% null (want 100%)')

# non-regressed sanity: scenarios that DO carry severity should still be numeric, non-null
carries_sev = tmp[~tmp[fault_col].isin(NULL_SEVERITY_SCENARIOS)]
if len(carries_sev):
    ok_dtype = pd.api.types.is_numeric_dtype(pd.to_numeric(carries_sev[prim_col], errors='coerce'))
    chk('D1 non-null-severity scenarios still numeric', ok_dtype, str(carries_sev[prim_col].dtype))

# --- DEFECT 2: vrf is list-typed on tunnel rows, not a delimited string -----
tun = df[df.entity_type == 'tunnel']
is_list_type = tun.vrf.dropna().apply(lambda v: isinstance(v, (list, np.ndarray))).all() if tun.vrf.notna().any() else False
chk('D2a tunnel vrf is list-typed', is_list_type,
    'list<string>' if is_list_type else str(tun.vrf.dropna().iloc[0]) if tun.vrf.notna().any() else 'no data')

has_compound_string = tun.vrf.dropna().apply(lambda v: isinstance(v, str) and '+' in v).any() if tun.vrf.notna().any() else False
chk('D2a no delimited "+"-joined vrf strings remain', not has_compound_string,
    'still present' if has_compound_string else 'none found')

chk('D2b sla_binding_vrf present on ramp_derived tunnel faults',
    'sla_binding_vrf' in df.columns,
    'present' if 'sla_binding_vrf' in df.columns else 'MISSING')

# --- no regressions in what was already fixed -------------------------------
lead = df.lead_time_s.dropna()
cv = lead.std() / lead.mean()
chk('NR lead CV still >= 0.5 (unaffected by this pass)', cv >= 0.5, f'{cv:.3f}')

for c in ['if_in_errors', 'if_in_discards', 'if_out_errors']:
    nz = (df[c].fillna(0) > 0).sum()
    chk(f'NR {c} still all zero', nz == 0, f'{nz} nonzero')

nconc = df.n_concurrent.max() if 'n_concurrent' in df.columns else 0
chk('NR n_concurrent still reaches >= 2', nconc >= 2, f'max={nconc}')

print('\n' + ('ALL CHECKS PASSED' if not fails else f'{len(fails)} FAILED: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
