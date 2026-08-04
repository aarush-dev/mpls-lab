"""signatures.py -- the ONE fault->signature table + ramp math.

Pure numpy, no I/O. Today's only importer is the dataset generator (synthetic/
calibrate.py + generate.py). The live controller (faults/) imports it too once
#59's controller-overlay ticket lands -- at which point live telemetry and
training data share the same peaks and ramp shape and can't silently diverge.
This module exists now so that landing is a pure import, not a re-derivation.

Three public functions, extracted byte-for-byte from the generator's former
inline closures (calibrate._fault_signatures / generate._prog / _tunnel_ramp):

  default_signatures(base_lat, base_loss, base_jit)
      per fault_type {lat_peak, loss_peak, jit_peak, lead_s, kind}
  prog(ep, t_start, t_impact, t_end, dur, sevmul, step, p_cross=1.0)
      piecewise-linear impairment fraction 0->1->0 (scalar OR array epoch)
  tunnel_ramp_targets(sig, lat, jit)
      floored (lat, jit) targets at 1.15x healthy; loss is the caller's
      additive loss_peak bump (no floor)
"""
import numpy as np


def default_signatures(base_lat, base_loss, base_jit):
    """Per fault_type peak signature. base_* are the healthy tunnel medians the
    relative peaks scale off. base_loss is unused by the current table (loss
    peaks are absolute) but kept in the signature for parity + future faults.
    """
    return {
        "congestion":     {"lat_peak": 60.0, "loss_peak": 3.0, "jit_peak": 8.0, "lead_s": 50.0, "kind": "tunnel_ramp"},
        "bgp_flap":       {"lat_peak": base_lat, "loss_peak": 0.3, "jit_peak": base_jit, "lead_s": 2.0, "kind": "iface_churn"},
        "tunnel_degrade": {"lat_peak": 35.0, "loss_peak": 5.0, "jit_peak": 12.0, "lead_s": 40.0, "kind": "tunnel_ramp"},
        "policy_drift":   {"lat_peak": 28.0, "loss_peak": 0.35, "jit_peak": 3.0, "lead_s": 3.0, "kind": "iface_churn"},
        "node_failure":   {"lat_peak": base_lat, "loss_peak": 1.0, "jit_peak": base_jit, "lead_s": 1.0, "kind": "iface_down"},
        "asymmetric_loss": {"lat_peak": base_lat * 1.1, "loss_peak": 4.0, "jit_peak": base_jit * 1.5, "lead_s": 30.0, "kind": "tunnel_ramp"},
        "brownout":       {"lat_peak": 45.0, "loss_peak": 1.5, "jit_peak": 6.0, "lead_s": 55.0, "kind": "tunnel_ramp"},
        # --- core / catastrophic / correlated (faults/orchestrator.py SCENARIOS) ---
        "p_node_failure":  {"lat_peak": base_lat * 1.3, "loss_peak": 2.0, "jit_peak": base_jit * 2.0, "lead_s": 5.0, "kind": "iface_down"},
        "pop_isolation":   {"lat_peak": base_lat * 1.6, "loss_peak": 6.0, "jit_peak": base_jit * 2.5, "lead_s": 3.0, "kind": "iface_down"},
        "core_partition":  {"lat_peak": base_lat * 1.8, "loss_peak": 8.0, "jit_peak": base_jit * 3.0, "lead_s": 3.0, "kind": "iface_down"},
        "srlg_cut":        {"lat_peak": base_lat * 1.4, "loss_peak": 4.0, "jit_peak": base_jit * 2.2, "lead_s": 2.0, "kind": "iface_down"},
        "core_congestion": {"lat_peak": 55.0, "loss_peak": 2.5, "jit_peak": 10.0, "lead_s": 45.0, "kind": "tunnel_ramp"},
        "ospf_area_flap":  {"lat_peak": base_lat * 1.2, "loss_peak": 1.0, "jit_peak": base_jit * 2.0, "lead_s": 4.0, "kind": "iface_churn"},
        "path_asymmetry":  {"lat_peak": 30.0, "loss_peak": 0.4, "jit_peak": 4.0, "lead_s": 20.0, "kind": "tunnel_ramp"},
        "rr_failure":      {"lat_peak": base_lat, "loss_peak": 1.2, "jit_peak": base_jit, "lead_s": 3.0, "kind": "iface_churn"},
        # gray_failure: weak tunnel signal, long precursor; the optics carry it.
        "gray_failure":    {"lat_peak": base_lat * 1.15, "loss_peak": 1.8, "jit_peak": base_jit * 1.3, "lead_s": 90.0, "kind": "tunnel_ramp"},
        "mpls_underlay_failure": {"lat_peak": base_lat * 1.3, "loss_peak": 2.0, "jit_peak": base_jit * 1.8, "lead_s": 4.0, "kind": "iface_down"},
        "ldp_session_flap":      {"lat_peak": base_lat * 1.1, "loss_peak": 0.8, "jit_peak": base_jit * 1.6, "lead_s": 3.0, "kind": "iface_churn"},
        "hub_spoke_congest":     {"lat_peak": 70.0, "loss_peak": 5.0, "jit_peak": 14.0, "lead_s": 40.0, "kind": "tunnel_ramp"},
        "bgp_cascade":           {"lat_peak": base_lat * 1.2, "loss_peak": 1.5, "jit_peak": base_jit * 2.0, "lead_s": 3.0, "kind": "iface_churn"},
        "controller_drift":      {"lat_peak": 38.0, "loss_peak": 3.0, "jit_peak": 5.0, "lead_s": 25.0, "kind": "tunnel_ramp"},
    }


def prog(ep, t_start, t_impact, t_end, dur, sevmul, step, p_cross=1.0):
    """Impairment fraction over one episode. Piecewise linear through four knots:

        t_start          0          healthy
        t_impact         p_cross    the SLA threshold is reached
        t_impact+0.3dur  1          the signature's calibrated peak
        t_end            0          recovered

    ep is an epoch (seconds) scalar OR array; returns the same shape * sevmul.
    p_cross==1 means the signature never breaches SLA -> plain ramp-then-decay.
    """
    ep = np.asarray(ep, dtype=float)
    knots_t = [t_start, t_impact, t_impact + 0.3 * max(dur, step), t_end]
    knots_p = [0.0, p_cross, 1.0, 0.0]
    return np.clip(np.interp(ep, knots_t, knots_p), 0.0, 1.0) * sevmul


def tunnel_ramp_targets(sig, lat, jit):
    """Floored latency/jitter targets the ramp moves toward at full impairment.

    Floored at 1.15x the healthy value: several calibrated peaks sit BELOW the
    generated healthy mean (diurnal congestion raises it), so ramping straight
    at them would ramp DOWNWARD -- a fault that looks healthier than healthy.
    lat/jit may be scalars or arrays; targets match their shape. Loss is NOT
    floored (loss_peak is an absolute additive bump: loss + p*loss_peak), so the
    caller adds it directly off sig["loss_peak"].
    """
    return np.maximum(sig["lat_peak"], lat * 1.15), np.maximum(sig["jit_peak"], jit * 1.15)
