"""leadpriors.py -- per-fault-type PRECURSOR LEAD priors, in buckets.

DEFECT 1. The lead time used to come from `calibrate.py`, which took the median
`lead_time_s` of a 24.5-minute capture at 30 s resolution. That produced ~2 s
leads for most types; the generator's 4-bucket safety floor then clamped 100% of
them to exactly 120 s, so `lead_time_s` had CV 0.03 and `time_to_impact_s` took
one of four values on 98.2% of precursor rows. A hazard head cannot learn a
distribution from a constant.

Ranges are expressed in BUCKETS, not seconds, so they stay correct when --step
changes. They are engineering priors on how far ahead each failure mode is
visible in telemetry, not measurements -- see docs/SPEC-NOTES.md for the
reasoning per group.

Draw: lognormal with the 10th/90th percentiles pinned to the range endpoints, so
~80% of episodes land inside the prior and the tails stay physical (no negative
lead, no hard cap). CV per type ~0.37; across types ~0.6.

Used by BOTH paths so they cannot drift: synthetic/generate.py draws the ramp
span from here, faults/orchestrator.py draws the live injector's ramp duration
from here.
"""
import math

# fault type -> (lo_buckets, hi_buckets) at the 10th/90th percentile
LEAD_BUCKETS = {
    # control-plane events: reconvergence churn is visible only shortly before
    # the session actually drops
    "bgp_flap": (4, 10),
    "ldp_session_flap": (4, 10),
    "node_failure": (4, 10),
    "rr_failure": (4, 10),
    "p_node_failure": (4, 10),
    "ospf_area_flap": (4, 10),
    "bgp_cascade": (4, 10),
    # congestion: queues fill slowly, so the precursor is long
    "congestion": (10, 40),
    "core_congestion": (10, 40),
    "hub_spoke_congest": (10, 40),
    "brownout": (10, 40),
    # overlay / policy degradation: medium horizon
    "tunnel_degrade": (8, 30),
    "asymmetric_loss": (8, 30),
    "policy_drift": (8, 30),
    "path_asymmetry": (8, 30),
    "controller_drift": (8, 30),
    # underlay topology loss: some warning from IGP/LDP churn, then it is gone
    "mpls_underlay_failure": (6, 20),
    "pop_isolation": (6, 20),
    "core_partition": (6, 20),
    "srlg_cut": (6, 20),
    # physical-layer decay: the longest horizon in the set, and the reason the
    # SFF-8472 optical columns exist
    "gray_failure": (20, 80),
}

# DEFECT 1b. SLA objectives per VRF, used to define t_impact as the crossing
# INSIDE the ramp instead of an independent offset. Not derivable from the lab:
# generator/templates/qos.sh.j2 shapes BANDWIDTH only (HTB rate/ceil/prio, no
# latency or loss target), so these are stated policy objectives. The GUEST loss
# figure is the one number with an in-repo anchor -- it matches the controller's
# FAILOVER_LOSS_PCT (controller/controller.py), the point at which path selection
# abandons a tunnel.
THETA_SLA = {
    "VOICE": {"latency_ms": 150.0, "loss_pct": 1.0},
    "CORP":  {"latency_ms": 250.0, "loss_pct": 2.0},
    "GUEST": {"latency_ms": 400.0, "loss_pct": 5.0},
}
# A tunnel carries every VRF of its site, so the SLA that breaks first is the
# strictest one present.
_STRICT_ORDER = ["VOICE", "CORP", "GUEST"]


def strictest_sla(vrfs):
    """Tightest (latency_ms, loss_pct) across the VRFs riding one entity."""
    present = [v for v in _STRICT_ORDER if v in set(vrfs or ())] or ["CORP"]
    return (min(THETA_SLA[v]["latency_ms"] for v in present),
            min(THETA_SLA[v]["loss_pct"] for v in present))


def sla_binding_vrf(vrfs):
    """DEFECT 2b: which VRF's SLA is the binding (strictest) one on a multi-VRF
    tunnel. VOICE is tightest on BOTH latency and loss, so the strictest is just
    the first present in _STRICT_ORDER -- recorded per fault so t_impact's choice
    on a multi-VRF tunnel is auditable, not an accident of string parsing."""
    present = [v for v in _STRICT_ORDER if v in set(vrfs or ())] or ["CORP"]
    return present[0]


DEFAULT_RANGE = (8, 30)
FLOOR_BUCKETS = 4.0  # safety net only: a ramp shorter than this has no signal
_P90_Z = 1.2815515655446004  # standard-normal 90th percentile


def lognormal_params(ft):
    """(mu, sigma) of the lognormal whose p10/p90 are the prior's endpoints.

    The lower endpoint is lifted to 1.15x the floor where the prior would sit on
    it (the 4-10 bucket group): pinning p10 exactly at 4 buckets makes the safety
    net fire on ~10% of that group's episodes, and a net that catches a tenth of
    all draws is shaping the distribution, not guarding it.
    """
    lo, hi = LEAD_BUCKETS.get(ft, DEFAULT_RANGE)
    lo = max(lo, FLOOR_BUCKETS * 1.15)
    mu = (math.log(lo) + math.log(hi)) / 2.0
    sigma = (math.log(hi) - math.log(lo)) / (2.0 * _P90_Z)
    return mu, sigma


def draw_lead_s(ft, step, normal_draw):
    """Lead in SECONDS for one episode.

    `normal_draw` is a standard-normal sample supplied by the caller's own seeded
    RNG -- the generator's reproducibility depends on every draw coming from its
    single stream, so this module never owns an RNG.

    Returns (lead_s, floored) where `floored` says the safety net fired.
    """
    mu, sigma = lognormal_params(ft)
    buckets = math.exp(mu + sigma * float(normal_draw))
    floored = buckets < FLOOR_BUCKETS
    return max(buckets, FLOOR_BUCKETS) * float(step), floored


def _selftest():
    import random
    rnd = random.Random(0)
    for ft in ("bgp_flap", "congestion", "gray_failure"):
        lo, hi = LEAD_BUCKETS[ft]
        lo = max(lo, FLOOR_BUCKETS * 1.15)
        draws = [draw_lead_s(ft, 30, rnd.gauss(0, 1))[0] / 30 for _ in range(20000)]
        p10 = sorted(draws)[2000]
        p90 = sorted(draws)[18000]
        # p10 can be lifted by the floor on the short-lead types; p90 must land
        assert abs(p90 - hi) / hi < 0.08, (ft, p90, hi)
        assert p10 >= min(lo, FLOOR_BUCKETS) * 0.9, (ft, p10, lo)
    # spread is the entire point of this module
    d = [draw_lead_s("congestion", 30, rnd.gauss(0, 1))[0] for _ in range(5000)]
    mean = sum(d) / len(d)
    cv = (sum((x - mean) ** 2 for x in d) / len(d)) ** 0.5 / mean
    assert cv > 0.30, cv
    # the floor must be rare on every prior, not universal (the D1 regression)
    for ft in LEAD_BUCKETS:
        fl = sum(draw_lead_s(ft, 30, rnd.gauss(0, 1))[1] for _ in range(4000))
        assert fl / 4000 < 0.05, (ft, fl / 4000)
    print("leadpriors selftest OK")


if __name__ == "__main__":
    _selftest()
