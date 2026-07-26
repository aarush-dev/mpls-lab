"""envmodel.py -- device environmental / power / optical models.

SINGLE SOURCE OF TRUTH for the modelled (non-measurable) features. Imported by
BOTH the live sidecar (telemetry/env-metrics.py) and the synthetic generator
(synthetic/generate.py), so live and synthetic emit the same physics.

Containers have no thermal or power sensors, so these features are MODELLED, not
measured. Model shapes are taken from the literature, not invented:

  Power   Vishwanath, Hinton, Ayre, Tucker, "Modeling Energy Consumption in
          High-Capacity Routers and Switches", IEEE JSAC 2014. Measured Cisco
          CRS-3: 12.3 kW max vs 11.07 kW idle -> idle is ~90% of max. Router
          power is NOT proportional to load; it is a high constant plus a small
          load-dependent term. Mytton (J. Industrial Ecology, 2024) makes the
          same point against kWh/GB-style proportional models.
          => P = P_idle + (P_max - P_idle) * util,  P_idle = IDLE_FRAC * P_max

  Noise   arXiv 2602.22339 (2026) regresses real package power on 1400+ telemetry
          params over 10k machines and gets R^2 = 0.33. Real power is only weakly
          predictable from load. A noiseless P = f(util) would be a giveaway
          label rather than a sensor reading, so POWER_SIGMA_FRAC injects
          comparable residual variance.

  Thermal El-Sayed, Stefanovici, Amvrosiadis, Hwang, Schroeder, "Temperature
          Management in Data Centers: Why Some (Might) Like It Hot",
          SIGMETRICS 2012. Below ~50 C internal device temperature, error rates
          grow LINEARLY with temperature, not exponentially as Arrhenius
          predicts. So temp_failure_scale() is linear, not Arrhenius.

  Optical SFF-8472 (DOM/DDM). The five standard fields are temperature, VCC,
          laser bias current, Tx power, Rx power. A rising Tx bias current is
          the canonical weeks-ahead predictor of laser end-of-life: as the diode
          ages its quantum efficiency drops, so the control loop pushes more
          bias current to hold output power constant.

  Sensor  RFC 3433 ENTITY-SENSOR-MIB is how real routers expose temperature,
  shape   fan, voltage and power over SNMP. Metric names below mirror that
          model so the dataset is structurally comparable to real gear.

# ponytail: pure functions + module constants, no I/O and no classes. Callers own
#   their own RNG and their own previous-value state (thermal mass needs it).
"""

# --- Power -----------------------------------------------------------------
# Per-role chassis max draw (W). Order-of-magnitude plausible for the class of
# device each role represents; the SHAPE (idle-dominated) is what matters and is
# what the literature pins down.
ROLE_PMAX_W = {
    "core":   3200.0,   # P router: big chassis LSR
    "pe":     1650.0,   # PE router: edge LER
    "hub":     180.0,   # hub CPE
    "dc":      180.0,   # dc CPE
    "branch":   72.0,   # branch CPE
}
DEFAULT_PMAX_W = 180.0

# Vishwanath 2014: measured idle/max = 11.07/12.3 = 0.90 on a CRS-3. 0.87 is a
# slightly conservative value across the device classes above.
IDLE_FRAC = 0.87

# Residual spread as a fraction of the load-dependent band, so the load->power
# relation stays learnable but far from deterministic (see arXiv 2602.22339).
POWER_SIGMA_FRAC = 0.35


def power_watts(role, util, gauss):
    """Chassis power draw (W). `gauss` is a caller-supplied N(0,1) sample.

    P = P_idle + (P_max - P_idle) * util + noise, with P_idle = IDLE_FRAC * P_max.
    """
    pmax = ROLE_PMAX_W.get(role, DEFAULT_PMAX_W)
    pidle = pmax * IDLE_FRAC
    band = pmax - pidle
    return max(0.0, pidle + band * _clamp01(util) + gauss * band * POWER_SIGMA_FRAC)


# --- Thermal ---------------------------------------------------------------
AMBIENT_C = 22.0        # nominal cold-aisle inlet temperature
AMBIENT_SPREAD_C = 4.0  # per-POP spread around nominal (see pop_ambient_c)
K_LOAD_C = 12.0         # full-load rise above ambient
THERMAL_ALPHA = 0.2     # thermal mass: EMA pull toward target per tick
TEMP_SIGMA_C = 0.35

# Linear error-rate coupling (El-Sayed 2012), NOT Arrhenius.
TEMP_BETA = 0.03        # relative error-rate increase per degree C above ref
TEMP_REF_C = 25.0


def pop_ambient_c(pop_index):
    """Ambient inlet temperature shared by every device in one POP.

    Deliberately per-POP, not per-device: real racks in a facility heat together,
    so this makes temperature a SPATIALLY CORRELATED feature across the topology
    graph. That correlation is the point -- it gives a graph-aware model
    (the GNN objective) a signal that per-device metrics cannot provide.
    """
    # Deterministic spread across POPs; no RNG so live and synthetic agree.
    frac = ((pop_index * 0.6180339887) % 1.0)     # golden-ratio sequence
    return AMBIENT_C + (frac - 0.5) * 2.0 * AMBIENT_SPREAD_C


def temp_c(prev_c, ambient_c, util, fault_heat_c, gauss):
    """Chassis temperature (C) for this tick, with thermal mass.

    Target is ambient + load rise + fault heat; the returned value moves only
    part-way there, so temperature LAGS load. That lag is realistic and is what
    makes temperature a usable precursor rather than a restatement of util.
    """
    target = ambient_c + K_LOAD_C * _clamp01(util) + fault_heat_c + gauss * TEMP_SIGMA_C
    return prev_c + THERMAL_ALPHA * (target - prev_c)


def temp_failure_scale(temp):
    """Relative hardware error-rate multiplier at a given temperature.

    LINEAR in temperature (El-Sayed 2012 field data), not the exponential the
    Arrhenius model predicts. 1.0 at TEMP_REF_C, never below 1.0.
    """
    return max(1.0, 1.0 + TEMP_BETA * (temp - TEMP_REF_C))


# Extra chassis heat while a fault is active. Faults that push load onto the
# device heat it; a fault that KILLS a process removes load, so it cools.
FAULT_HEAT_C = {
    "core_congestion":  6.0,
    "congestion":       5.0,
    "brownout":         5.0,
    "hub_spoke_congest": 5.0,
    "p_node_failure":   4.0,   # neighbours absorb the rerouted load
    "srlg_cut":         4.0,
    "pop_isolation":    4.0,
    "core_partition":   4.0,
    "tunnel_degrade":   2.5,
    "asymmetric_loss":  1.5,
    "gray_failure":     1.0,
    "node_failure":    -3.0,   # bgpd dead -> less work -> cools toward ambient
    "rr_failure":      -3.0,
}


def fault_heat_c(fault_type):
    return FAULT_HEAT_C.get(fault_type, 0.0)


# --- Fan / PSU (ENTITY-SENSOR-MIB companions) -------------------------------
FAN_BASE_RPM = 3000.0
FAN_RPM_PER_C = 120.0       # ramps once above FAN_KNEE_C
FAN_KNEE_C = 30.0
PSU_NOMINAL_V = 12.0
PSU_SAG_V = 0.25            # full-load rail sag


def fan_rpm(temp):
    """Fan speed tracks temperature above a knee -- a second, lagging thermal view."""
    return FAN_BASE_RPM + FAN_RPM_PER_C * max(0.0, temp - FAN_KNEE_C)


def psu_voltage_v(util, gauss):
    """12 V rail, sagging slightly under load."""
    return PSU_NOMINAL_V - PSU_SAG_V * _clamp01(util) + gauss * 0.02


# --- Optical transceiver (SFF-8472 DOM) -------------------------------------
XCVR_BIAS_BASE_MA = 28.0
XCVR_BIAS_AGE_MA = 6.0       # bias climb over full modelled service life
XCVR_TX_DBM = -2.5
XCVR_RX_BASE_DBM = -6.0
XCVR_RX_FLOOR_DBM = -18.0    # below this the link errors then drops
XCVR_TEMP_OFFSET_C = 8.0     # optic sits hotter than chassis inlet


def optical(chassis_temp_c, age_frac, degrade, gauss):
    """(xcvr_temp_c, rx_power_dbm, tx_bias_ma) for one transceiver.

    age_frac in [0,1] = fraction of modelled service life consumed.
    degrade  in [0,1] = active physical-layer degradation (a gray failure).

    Bias current rises with BOTH age and degradation while received power falls:
    that divergence is the signature a predictive model should learn, and it is
    the one precursor that appears before any link-down event.
    """
    t = chassis_temp_c + XCVR_TEMP_OFFSET_C + gauss * 0.3
    bias = (XCVR_BIAS_BASE_MA
            + XCVR_BIAS_AGE_MA * _clamp01(age_frac)
            + 9.0 * _clamp01(degrade)
            + gauss * 0.15)
    rx = (XCVR_RX_BASE_DBM
          - 1.2 * _clamp01(age_frac)
          - (XCVR_RX_BASE_DBM - XCVR_RX_FLOOR_DBM) * _clamp01(degrade)
          + gauss * 0.25)
    return t, rx, bias


def _clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


# --- role helper ------------------------------------------------------------
def role_of(device, site_type=None):
    """Map a device name (or its site_type) onto a power/thermal role."""
    if site_type in ROLE_PMAX_W:
        return site_type
    d = str(device)
    if d.startswith("pe"):
        return "pe"
    if d.startswith("p"):
        return "core"
    if d.startswith("ce_hub"):
        return "hub"
    if d.startswith("ce_dc"):
        return "dc"
    return "branch"


if __name__ == "__main__":
    # ponytail: one runnable check covering every non-trivial branch.
    import random
    rng = random.Random(7)

    # Idle-dominated power: near-zero load still draws most of max (Vishwanath).
    p_idle = power_watts("core", 0.0, 0.0)
    p_full = power_watts("core", 1.0, 0.0)
    assert abs(p_idle - ROLE_PMAX_W["core"] * IDLE_FRAC) < 1e-6, p_idle
    assert abs(p_full - ROLE_PMAX_W["core"]) < 1e-6, p_full
    assert p_idle / p_full > 0.8, "power must be idle-dominated, not proportional"

    # Roles are ordered: a core chassis outdraws a branch CPE at equal load.
    assert power_watts("core", 0.5, 0.0) > power_watts("branch", 0.5, 0.0)

    # Thermal mass: temperature LAGS a load step rather than jumping to target.
    amb = pop_ambient_c(1)
    t = amb
    t1 = temp_c(t, amb, 1.0, 0.0, 0.0)
    assert t < t1 < amb + K_LOAD_C, f"no thermal lag: {t1}"
    for _ in range(200):                      # settle
        t1 = temp_c(t1, amb, 1.0, 0.0, 0.0)
    assert abs(t1 - (amb + K_LOAD_C)) < 0.1, f"did not settle to target: {t1}"

    # Per-POP ambient really differs between POPs (spatial correlation exists).
    ambs = [pop_ambient_c(i) for i in range(1, 7)]
    assert len(set(ambs)) == 6 and max(ambs) - min(ambs) > 1.0, ambs

    # Failure coupling is linear and floored at 1.0 (El-Sayed, not Arrhenius).
    assert temp_failure_scale(TEMP_REF_C) == 1.0
    assert temp_failure_scale(15.0) == 1.0, "must not go below 1.0 when cool"
    lo = temp_failure_scale(35.0) - temp_failure_scale(25.0)
    hi = temp_failure_scale(45.0) - temp_failure_scale(35.0)
    assert abs(lo - hi) < 1e-9, "coupling must be linear, not exponential"

    # Fault heat: congestion heats, a killed daemon cools.
    assert fault_heat_c("core_congestion") > 0 > fault_heat_c("node_failure")
    assert fault_heat_c("no_such_fault") == 0.0

    # Fan is flat below the knee, then ramps with temperature.
    assert fan_rpm(20.0) == fan_rpm(FAN_KNEE_C) == FAN_BASE_RPM
    assert fan_rpm(50.0) > fan_rpm(40.0) > FAN_BASE_RPM

    # PSU rail sags under load.
    assert psu_voltage_v(1.0, 0.0) < psu_voltage_v(0.0, 0.0) <= PSU_NOMINAL_V

    # Optical: degradation drives bias UP while received power falls (SFF-8472).
    _, rx0, b0 = optical(30.0, 0.0, 0.0, 0.0)
    _, rx1, b1 = optical(30.0, 0.0, 1.0, 0.0)
    assert b1 > b0 and rx1 < rx0, (b0, b1, rx0, rx1)
    _, rxa, ba = optical(30.0, 1.0, 0.0, 0.0)
    assert ba > b0 and rxa < rx0, "ageing must raise bias and lower rx power"
    # Optic runs hotter than the chassis.
    assert optical(30.0, 0.0, 0.0, 0.0)[0] > 30.0

    # Role mapping covers every device family in the lab.
    assert role_of("p12") == "core" and role_of("pe3") == "pe"
    assert role_of("ce_hub2") == "hub" and role_of("ce_dc1") == "dc"
    assert role_of("ce_branch9") == "branch"
    assert role_of("anything", site_type="pe") == "pe"

    print("envmodel selftest: OK")
