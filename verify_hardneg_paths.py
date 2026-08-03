# verify_hardneg_paths.py  --  run: python verify_hardneg_paths.py <main.parquet> <paths.parquet>
import sys, numpy as np, pandas as pd, pyarrow.parquet as pq

main_f, paths_f = sys.argv[1], sys.argv[2]
df = pq.read_table(main_f).to_pandas()
pa = pq.read_table(paths_f).to_pandas()
fails = []
def chk(name, cond, got):
    print(('PASS  ' if cond else 'FAIL  ') + f'{name:<62} {got}')
    if not cond: fails.append(name)

# --- DEFECT 1: hard-negative label purity ---
hn = df[df.is_hard_negative].copy()
def tti_all_null(x):
    if isinstance(x, (list, np.ndarray)):
        return all(v is None or (isinstance(v, float) and np.isnan(v)) for v in x)
    return x is None or (isinstance(x, float) and np.isnan(x))
hn['tti_null'] = hn.time_to_impact_s.apply(tti_all_null)
leaked = hn[~hn.tti_null]
chk('D1 zero hard-negative rows with a real time_to_impact_s', len(leaked) == 0,
    f'{len(leaked)} leaked rows (was 3,402)')

sev_col = 'severity_primary' if 'severity_primary' in hn.columns else 'severity'
sev_leak = hn[hn[sev_col].notna()]
chk('D1 zero hard-negative rows with a real severity', len(sev_leak) == 0,
    f'{len(sev_leak)} rows')

def im_all_null_or_none(x):
    if isinstance(x, (list, np.ndarray)):
        return all(v is None or v == 'no_impact' for v in x)
    return x is None or x == 'no_impact'
im_leak = hn[~hn.impact_methods.apply(im_all_null_or_none)]
chk('D1 zero hard-negative rows with a real impact_method', len(im_leak) == 0,
    f'{len(im_leak)} rows')

# --- DEFECT 2: paths.parquet reflects real intervals ---
chk('D2 paths.parquet has at least one still-active row (valid_to IS NULL)',
    pa.valid_to.isna().any(), f'{100*pa.valid_to.isna().mean():.1f}% null (was 0.0%)')

group_col = 'path_id' if pa.path_id.duplicated().any() else \
            ('tunnel_id' if 'tunnel_id' in pa.columns else 'path_id')
sizes = pa.groupby(group_col).size()
chk('D2 at least one path group has multiple interval rows (a captured reroute)',
    (sizes > 1).any(), f'max group size={sizes.max()} (was 1 everywhere)')

# --- no regressions ---
lead = df.lead_time_s.dropna()
chk('NR lead CV still high', lead.std()/lead.mean() >= 0.5, f'{lead.std()/lead.mean():.3f}')
chk('NR topology diversity still 12', df.topology_id.nunique() == 12, f'{df.topology_id.nunique()}')
chk('NR stream F/N still clean', df[df.stream=='N'].is_fault.mean() == 0.0,
    f'{100*df[df.stream=="N"].is_fault.mean():.3f}% (want 0%)')

print('\n' + ('ALL CHECKS PASSED' if not fails else f'{len(fails)} FAILED: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
