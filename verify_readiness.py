# verify_readiness.py  --  run: python verify_readiness.py <new.parquet> [<events.parquet>] [<topology_edges.parquet>] [<paths.parquet>]
import sys, numpy as np, pandas as pd, pyarrow.parquet as pq

f = sys.argv[1]
df = pq.read_table(f).to_pandas()
fails = []
def chk(name, cond, got):
    print(('PASS  ' if cond else 'FAIL  ') + f'{name:<58} {got}')
    if not cond: fails.append(name)

# --- G1: topology diversity ---
chk('G1 >= 8 distinct topology_id values',
    'topology_id' in df.columns and df.topology_id.nunique() >= 8,
    df.topology_id.nunique() if 'topology_id' in df.columns else 'topology_id column MISSING')

# --- G4: hard negatives present ---
chk('G4 is_hard_negative column present', 'is_hard_negative' in df.columns,
    'present' if 'is_hard_negative' in df.columns else 'MISSING')
if 'is_hard_negative' in df.columns:
    hn = df.is_hard_negative.sum()
    chk('G4 hard negatives > 0', hn > 0, f'{hn} rows')

# --- G6: stream tagging (however you choose to expose it, e.g. a `stream` column) ---
chk('G6 stream column present (F/N)', 'stream' in df.columns,
    'present' if 'stream' in df.columns else 'MISSING -- confirm sampler-side prevalence composition instead')

# --- G7: cascade/root columns ---
for col in ['is_root','cascade_parent_id','cascade_depth','cascade_motif_id','affected_entity_count']:
    chk(f'G7 {col} present', col in df.columns, 'present' if col in df.columns else 'MISSING')

# --- G8: injection_seed ---
chk('G8 injection_seed present', 'injection_seed' in df.columns,
    'present' if 'injection_seed' in df.columns else 'MISSING')

# --- no regressions from prior passes ---
lead = df.lead_time_s.dropna()
chk('NR lead CV still >= 0.5', lead.std()/lead.mean() >= 0.5, f'{lead.std()/lead.mean():.3f}')
for c in ['if_in_errors','if_in_discards','if_out_errors']:
    nz = (df[c].fillna(0) > 0).sum()
    chk(f'NR {c} still all zero', nz == 0, f'{nz} nonzero')
is_list_vrf = df.vrf.dropna().apply(lambda v: isinstance(v,(list,np.ndarray))).all() if df.vrf.notna().any() else False
chk('NR vrf still list-typed', is_list_vrf, 'list' if is_list_vrf else 'REGRESSED to scalar')
NULL_SEV = {"node_failure","p_node_failure","pop_isolation","core_partition","rr_failure"}
fault_col = 'fault_type_primary' if 'fault_type_primary' in df.columns else 'fault_type'
prim_col = 'severity_primary' if 'severity_primary' in df.columns else 'severity'
tmp = df[df.is_fault]
for scen in NULL_SEV:
    sub = tmp[tmp[fault_col]==scen]
    if len(sub):
        nr = sub[prim_col].isna().mean()
        chk(f'NR {scen} severity still null', nr == 1.0, f'{100*nr:.1f}% null')

# --- G2/G3: separate files, if provided ---
if len(sys.argv) > 2:
    ev = pq.read_table(sys.argv[2]).to_pandas()
    chk('G2 events.parquet has templated ids, not raw text',
        'template_id' in ev.columns, 'present' if 'template_id' in ev.columns else 'MISSING')
if len(sys.argv) > 3:
    te = pq.read_table(sys.argv[3]).to_pandas()
    chk('G3 topology_edges.parquet has interval encoding',
        {'valid_from','valid_to'}.issubset(te.columns), 'present' if {'valid_from','valid_to'}.issubset(te.columns) else 'MISSING')
if len(sys.argv) > 4:
    pa = pq.read_table(sys.argv[4]).to_pandas()
    chk('G3 paths.parquet hop_sequence is ordered list',
        'hop_sequence' in pa.columns and pa.hop_sequence.dropna().apply(lambda v: isinstance(v,(list,np.ndarray))).all(),
        'present' if 'hop_sequence' in pa.columns else 'MISSING')

print('\n' + ('ALL CHECKS PASSED' if not fails else f'{len(fails)} FAILED: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
