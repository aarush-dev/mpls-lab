# verify_full_generation.py -- run: python verify_full_generation.py <main.parquet | main_dir>
#
# Full-scale acceptance. A single main.parquet loads into pandas; a full-run
# tranche is ~100M rows and would OOM, so a directory input is streamed in
# batches with small accumulators (per-type scenario-id sets are ~20k strings,
# never the 100M rows). Same checks either way.
import sys, os, numpy as np, pyarrow as pa, pyarrow.dataset as ds, pyarrow.parquet as pq

path = sys.argv[1]
fails = []
def chk(name, cond, got):
    print(('PASS  ' if cond else 'FAIL  ') + f'{name:<62} {got}')
    if not cond: fails.append(name)

COLS = ["is_fault", "fault_type_primary", "scenario_id_primary", "topology_id",
        "is_hard_negative", "n_concurrent", "cascade_parent_id", "lead_time_s",
        "if_in_errors", "if_in_discards", "if_out_errors", "vrf", "stream"]

# accumulators
per_type = {}          # fault_type -> set(scenario_id_primary)
topos = set()
hn_rows = 0
concurrent_eps, cascade_eps = set(), set()
lead_n = lead_sum = lead_sqsum = 0.0
err_nz = {"if_in_errors": 0, "if_in_discards": 0, "if_out_errors": 0}
stream_stat = {}       # stream -> [fault_rows, total_rows]
vrf_is_list = True
vrf_seen = False
total_rows = 0

dataset = ds.dataset(path, format="parquet")
for batch in dataset.scanner(columns=COLS, batch_size=1_000_000).to_batches():
    total_rows += batch.num_rows
    d = batch.to_pydict()
    isf = d["is_fault"]; ft = d["fault_type_primary"]; sid = d["scenario_id_primary"]
    tid = d["topology_id"]; hn = d["is_hard_negative"]; nc = d["n_concurrent"]
    cpar = d["cascade_parent_id"]; lead = d["lead_time_s"]; strm = d["stream"]
    vrf = d["vrf"]
    for i in range(batch.num_rows):
        topos.add(tid[i])
        if hn[i]:
            hn_rows += 1
        st = strm[i]
        s = stream_stat.setdefault(st, [0, 0]); s[1] += 1
        if isf[i]:
            s[0] += 1
            per_type.setdefault(ft[i], set()).add(sid[i])
            if nc[i] and nc[i] >= 2:
                concurrent_eps.add(sid[i])
            if cpar[i] is not None:
                cascade_eps.add(sid[i])
        lv = lead[i]
        if lv is not None:
            lead_n += 1; lead_sum += lv; lead_sqsum += lv * lv
    for c in err_nz:
        col = d[c]
        err_nz[c] += sum(1 for v in col if v is not None and v > 0)
    if not vrf_seen:
        for v in vrf:
            if v is not None:
                vrf_is_list = isinstance(v, (list, np.ndarray)); vrf_seen = True; break

# --- fault instance volume, per type ---
under = {k: len(v) for k, v in per_type.items() if len(v) < 800}
chk('Fault-type volume: all 21 types >= 800 instances', len(under) == 0,
    f'{len(under)} under: {under}' if under else 'all >= 800')
chk('21 fault types present', len(per_type) == 21, f'{len(per_type)}')
chk('Topology count == 12', len(topos) == 12, f'{len(topos)}')
for s, (fr, tot) in sorted(stream_stat.items()):
    print(f'  stream={s} fault rate: {100*fr/tot:.3f}%  rows={tot}')
chk('Hard negatives >= 4,000 total', hn_rows >= 4000, f'{hn_rows}')
chk('Concurrent-pair episodes present', len(concurrent_eps) > 0, f'{len(concurrent_eps)}')
chk('Cascade episodes present', len(cascade_eps) > 0, f'{len(cascade_eps)}')
cv = (np.sqrt(lead_sqsum/lead_n - (lead_sum/lead_n)**2)) / (lead_sum/lead_n) if lead_n else 0
chk('lead_time_s CV >= 0.5', cv >= 0.5, f'{cv:.3f}')
for c, n in err_nz.items():
    chk(f'{c} still all zero', n == 0, f'{n} nonzero')
chk('vrf still list-typed', vrf_is_list, 'list' if vrf_is_list else 'REGRESSED')

# held_out KV metadata (from any part file)
part = path if path.endswith(".parquet") else \
    next(iter(ds.dataset(path, format="parquet").files), None)
if part:
    md = pq.ParquetFile(part).metadata.metadata or {}
    if b'held_out' in md:
        print('held_out metadata:', md[b'held_out'].decode())

print(f'\ntotal_rows={total_rows:,}')
print(('ALL CHECKS PASSED' if not fails else f'{len(fails)} FAILED: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
