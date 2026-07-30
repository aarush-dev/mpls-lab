"""verify_fixes.py -- acceptance gate for the six-defect repair.

run: python3 verify_fixes.py <train.parquet> <holdout.parquet>

Supplied by the audit. Two lines differ from the version as received, both
because the script as written cannot run against its own required output:

  * `tti`: exploding a list column duplicates index labels, so combining the
    exploded boolean with a row-level one raises "cannot reindex on an axis with
    duplicate labels" the moment any row carries two concurrent faults -- which
    DEFECT 5 requires. Element 0 of every list is the primary, so the ramp check
    reads that instead of exploding.
  * `ids()`: `s.iloc[0]` is NaN on a mostly-null label column, so the list check
    has to look at the first NON-null value.
"""
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

f, h = sys.argv[1], sys.argv[2]
pf = pq.ParquetFile(f); kv = {k.decode(): v.decode() for k, v in (pf.metadata.metadata or {}).items()}
df = pq.read_table(f).to_pandas()
ho = pq.read_table(h).to_pandas()
fails = []


def chk(name, cond, got):
    print(('PASS  ' if cond else 'FAIL  ') + f'{name:<52} {got}')
    if not cond:
        fails.append(name)


def _first(v):
    """Primary element of a label list column (element 0), else NaN."""
    if isinstance(v, (list, np.ndarray)) and len(v):
        return v[0]
    return v if isinstance(v, float) else np.nan


# --- DEFECT 1 : lead-time variance -------------------------------------------
lead = df.lead_time_s.dropna()
cv = lead.std() / lead.mean()
chk('D1 lead CV >= 0.50',            cv >= 0.50,                f'{cv:.3f}  (was 0.03)')
chk('D1 distinct lead values >= 100', lead.nunique() >= 100,     f'{lead.nunique()}  (was 9)')
tti_first = df.time_to_impact_s.map(_first)
tti = df.time_to_impact_s.explode().astype(float) \
      if df.time_to_impact_s.dtype == object else df.time_to_impact_s
pos = tti[tti > 0]
edges = np.geomspace(30, max(pos.max(), 60), 21)
occ = len(np.unique(np.clip(np.searchsorted(edges, pos), 0, 20)))
chk('D1 occupied hazard bins (J=20) >= 12', occ >= 12,           f'{occ}  (was 4)')
top4 = pos.value_counts(normalize=True).head(4).sum()
chk('D1 top-4 tti values < 40% of rows', top4 < 0.40,            f'{100*top4:.1f}%  (was 98.2%)')

# --- ramp must track the lead ------------------------------------------------
sub = df[(df.get('fault_type_primary', df.get('fault_type')) == 'congestion') &
         (df.entity_type == 'tunnel') & (tti_first > 0)].copy()
if len(sub) > 100:
    sub['k'] = sub.device + '|' + sub.entity
    sid = 'scenario_ids' if 'scenario_ids' in sub else 'scenario_id'
    sub[sid] = sub[sid].map(_first)
    r = sub.groupby([sid, 'k']).agg(lead=('lead_time_s', 'first'), n=('ts', 'size'))
    r = r[r.n >= 4]; r['span'] = (r.n - 1) * 30
    c = r.lead.corr(r.span)
    chk('D1b corr(lead, ramp span) >= 0.80', c >= 0.80,          f'{c:.3f}  (was ~0 in effect)')

# --- DEFECT 2 : zeroed error counters ---------------------------------------
for c in ['if_in_errors', 'if_in_discards', 'if_out_errors']:
    nz = (df[c].fillna(0) > 0).sum()
    chk(f'D2 {c} all zero', nz == 0,                             f'{nz} nonzero')
for c in ['if_out_discards', 'q_drops', 'q_backlog_bytes']:
    nz = (df[c].fillna(0) > 0).sum()
    chk(f'D2 {c} still populated', nz > 0,                       f'{nz} nonzero')

# --- DEFECT 3 : vrf ---------------------------------------------------------
chk('D3 vrf not all null',  df.vrf.notna().any(),                f'{100*df.vrf.notna().mean():.1f}% populated')
tv = df[df.entity_type == 'tunnel'].vrf
chk('D3 vrf set on tunnel rows', tv.notna().mean() > 0.95,       f'{100*tv.notna().mean():.1f}%')

# --- DEFECT 4 : flows -------------------------------------------------------
has_flow = 'flow_bytes' in df.columns
chk('D4 flow cols filled or dropped',
    (not has_flow) or df.flow_bytes.notna().any(),
    'dropped' if not has_flow else f'{df.flow_bytes.notna().sum()} nonnull')

# --- DEFECT 5 : concurrency + multi-label ----------------------------------
chk('D5b multi-label list columns present', 'fault_types' in df.columns,
    'yes' if 'fault_types' in df.columns else 'MISSING')
if 'n_concurrent' in df.columns:
    mx = int(df.n_concurrent.max())
    chk('D5a some window has >= 2 concurrent faults', mx >= 2,   f'max n_concurrent={mx}  (was 1)')

# --- DEFECT 6 : dtypes + metadata ------------------------------------------
sev = df.severities.explode().dropna() if 'severities' in df.columns else df.severity.dropna()
chk('D6a severity numeric', pd.api.types.is_numeric_dtype(pd.to_numeric(sev, errors='coerce')),
    str(sev.dtype))
chk('D6b ts is a timestamp', pd.api.types.is_datetime64_any_dtype(df.ts), str(df.ts.dtype))
chk('D6c seed in file KV',            'seed' in kv,              kv.get('seed', 'MISSING'))
chk('D6c calibrated_from in file KV', 'calibrated_from' in kv,   kv.get('calibrated_from', 'MISSING'))

# --- holdout integrity ------------------------------------------------------
sid_col = 'scenario_ids' if 'scenario_ids' in df.columns else 'scenario_id'


def ids(x):
    s = x[sid_col].dropna()
    if len(s) and isinstance(s.iloc[0], (list, np.ndarray)):
        return set(s.explode().dropna())
    return set(s)


a, b = ids(df), ids(ho)
chk('H episode sets disjoint', len(a & b) == 0,                  f'{len(a & b)} shared')
ratio = ho.if_in_octets.median() / df.if_in_octets.median()
chk('6d holdout load within 20% of train', 0.8 <= ratio <= 1.25, f'ratio={ratio:.3f}  (was 0.32)')

# --- regressions that must not appear --------------------------------------
iface = df[df.entity_type == 'interface']
back = sum(np.any(np.diff(g.sort_values('ts').if_in_octets.values) < 0)
           for _, g in iface.groupby([iface.device, iface.entity]))
chk('NR counters never step backwards', back == 0,               f'{back} keys')
cnt = iface.groupby([iface.device, iface.entity]).size().unique()
chk('NR uniform row count per key', len(cnt) == 1,               f'{cnt[:3]}')

print('\n' + ('ALL CHECKS PASSED' if not fails else f'{len(fails)} FAILED: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
