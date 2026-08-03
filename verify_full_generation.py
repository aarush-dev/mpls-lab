# verify_full_generation.py -- run: python verify_full_generation.py <main.parquet>
import sys, numpy as np, pandas as pd, pyarrow.parquet as pq

f = sys.argv[1]
df = pq.read_table(f).to_pandas()
fails = []
def chk(name, cond, got):
    print(('PASS  ' if cond else 'FAIL  ') + f'{name:<62} {got}')
    if not cond: fails.append(name)

# --- fault instance volume, per type ---
faulty = df[df.is_fault]
per_type = faulty.groupby('fault_type_primary').scenario_id_primary.nunique()
under_target = per_type[per_type < 800]
chk('Fault-type volume: all 21 types >= 800 instances', len(under_target) == 0,
    f'{len(under_target)} types under target: {under_target.to_dict()}' if len(under_target) else 'all 21 >= 800')
chk('21 fault types present', faulty.fault_type_primary.nunique() == 21,
    f'{faulty.fault_type_primary.nunique()}')

# --- topology count ---
chk('Topology count == 12', df.topology_id.nunique() == 12, f'{df.topology_id.nunique()}')

# --- stream prevalence sanity ---
for s in ['F', 'N']:
    sub = df[df.stream == s]
    if len(sub):
        print(f'  stream={s} fault rate: {100*sub.is_fault.mean():.3f}%  rows={len(sub)}')

# --- hard negatives, concurrency, cascades still present at volume ---
chk('Hard negatives >= 4,000 total', df.is_hard_negative.sum() >= 4000, f'{df.is_hard_negative.sum()}')
concurrent_eps = faulty[faulty.n_concurrent >= 2].scenario_id_primary.nunique()
chk('Concurrent-pair episodes present', concurrent_eps > 0, f'{concurrent_eps} episodes with n_concurrent>=2')
cascade_eps = faulty[faulty.cascade_parent_id.notna()].scenario_id_primary.nunique()
chk('Cascade episodes present', cascade_eps > 0, f'{cascade_eps}')

# --- no regressions from prior passes ---
lead = df.lead_time_s.dropna()
chk('lead_time_s CV >= 0.5', lead.std()/lead.mean() >= 0.5, f'{lead.std()/lead.mean():.3f}')
for c in ['if_in_errors','if_in_discards','if_out_errors']:
    nz = (df[c].fillna(0) > 0).sum()
    chk(f'{c} still all zero', nz == 0, f'{nz} nonzero')
is_list_vrf = df.vrf.dropna().apply(lambda v: isinstance(v,(list,np.ndarray))).all() if df.vrf.notna().any() else False
chk('vrf still list-typed', is_list_vrf, 'list' if is_list_vrf else 'REGRESSED')

# --- held-out topologies present for LOTO ---
md = pq.ParquetFile(f).metadata.metadata or {}
if b'held_out' in md:
    print('held_out metadata present:', md[b'held_out'].decode())

print('\n' + ('ALL CHECKS PASSED' if not fails else f'{len(fails)} FAILED: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
