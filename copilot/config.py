"""copilot.config -- the one master config every copilot component reads.

Typed (frozen dataclass), loaded from `copilot/config.yaml`, with secrets pulled
from the environment / `.env` (gitignored) and NEVER from the committed YAML.

The dataclass field defaults are the single source of truth for documented
defaults; `config.yaml` overlays operational overrides on top. `load()` merges
   defaults <- config.yaml <- env secrets
and validates enums/ranges. Missing YAML is fine -> pure defaults.

Field provenance (see docs/adr/):
  llm_profile / embed_profile   ADR-0004 / ADR-0006  (nim | unsloth-local)
  emulate_pa / error_profile    ADR-0003             (emulator seam; oracle|light|heavy)
  kg_enabled                    ADR-0007             (default-on, never critical-path)
  ledger_to_kb                  ADR-0009             (case verdict -> KB, default on)
  history_compaction            ADR-0015             (default off)
  window_x_min (X) /            ADR-0002             (rolling/forensic minutes; salvage cap)
  window_x_max (X_max)
  gate_min_evidence (N) /       ADR-0008             (pre-gate evidence floor + retry cap)
  gate_max_retries
  step_cap / tool_call_cap      ADR-0005             (agent-loop runaway guards)
  predict_interval_s            ADR-0014             (predict-loop cadence)

Usage:  from copilot.config import load;  cfg = load()
Self-check:  python3 copilot/config.py
"""
import dataclasses
import os
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_YAML = os.path.join(HERE, "config.yaml")

_LLM_PROFILES = frozenset({"nim", "unsloth-local"})
_ERROR_PROFILES = frozenset({"oracle", "light", "heavy"})
# ADR-0003 R0-R5 ladder, mirrored (not imported) from gate.DRIFT_LADDER: importing gate here is a
# cycle (config <- gate <- tools <- retrieval <- embedder <- config). A frozen 6-rung taxonomy.
_DRIFT_RUNGS = frozenset({"R0", "R1", "R2", "R3", "R4", "R5"})

# yaml carries these; env carries the rest (secrets). Kept explicit so a secret
# accidentally committed to config.yaml is REJECTED, not silently loaded.
_SECRET_FIELDS = frozenset({"llm_api_key", "embed_api_key"})
# env var name for each secret field
_SECRET_ENV = {
    "llm_api_key": "COPILOT_LLM_API_KEY",
    "embed_api_key": "COPILOT_EMBED_API_KEY",
}


@dataclass(frozen=True)
class Config:
    # -- profiles (ADR-0004 / ADR-0006): swap backend with one line ------------
    llm_profile: str = "nim"          # nim (interim) | unsloth-local (final, air-gapped)
                                      # read by llm.make_client (R1) to pick the backend
    embed_profile: str = "nim"        # follows the LLM profile pattern

    # -- feature flags --------------------------------------------------------
    emulate_pa: bool = True           # ADR-0003: emulator feeds records until real PA exists
    kg_enabled: bool = True           # ADR-0007: curated KG, additive, never critical-path
    ledger_to_kb: bool = True         # ADR-0009: finalized case verdict feeds the KB
    history_compaction: bool = False  # ADR-0015: summarize old turns (default off)
    history_max_chars: int = 6000     # ADR-0015 §5 / I6: when history_compaction is on, the char
                                      # budget the resumed history is kept under (loop.compact_history)

    # -- knobs ----------------------------------------------------------------
    window_x_min: int = 10            # ADR-0002: X, rolling/forensic window minutes
    window_x_max: int = 60            # ADR-0002: X_max, salvage lookback cap (minutes)
    gate_min_evidence: int = 2        # ADR-0008: N, pre-gate evidence floor
    gate_max_retries: int = 2         # ADR-0008: agentic retries on gate fail
    step_cap: int = 8                 # ADR-0005: max loop turns per investigation
    tool_call_cap: int = 6            # ADR-0005: max tool invocations per investigation
    error_profile: str = "light"      # ADR-0003: oracle | light | heavy
    drift_distrust_at: str = "R3"     # ADR-0003/story 14 (T1): trust gate flags the answer at/above
                                      # this R0-R5 model-health rung; "R5" = only the worst rung flags
    predict_interval_s: int = 3       # ADR-0014: predict-loop cadence (seconds). #48: safe at 3s --
                                      # case creation is cause-gated + idempotent by alert_id, so the
                                      # expensive adapter-drain+agent run is decoupled from tick rate.
    dataapi_url: str = "http://127.0.0.1:8000"  # A1/ADR-0006: base URL the HttpAdapter reads
                                                # (env COPILOT_DATAAPI_URL overrides, app.py)
    exec_timeout_s: int = 30          # ADR-0013/B2: default wall-clock cap on an exec run
    exec_max_timeout_s: int = 300     # ADR-0013/B2: ceiling a per-call timeout is clamped to
    exec_output_cap: int = 65536      # B2: chars of stdout/stderr kept per run (rest truncated)

    # -- secrets (from env / .env only; never committed) ----------------------
    llm_api_key: str = field(default="", repr=False)  # sent as Bearer by llm.OpenAIClient (R1)
    embed_api_key: str = field(default="", repr=False)

    def __post_init__(self):
        assert self.llm_profile in _LLM_PROFILES, \
            f"llm_profile={self.llm_profile!r} not in {sorted(_LLM_PROFILES)}"
        assert self.embed_profile in _LLM_PROFILES, \
            f"embed_profile={self.embed_profile!r} not in {sorted(_LLM_PROFILES)}"
        assert self.error_profile in _ERROR_PROFILES, \
            f"error_profile={self.error_profile!r} not in {sorted(_ERROR_PROFILES)}"
        assert self.drift_distrust_at in _DRIFT_RUNGS, \
            f"drift_distrust_at={self.drift_distrust_at!r} not a rung in {sorted(_DRIFT_RUNGS)}"
        assert self.window_x_min > 0, "window_x_min must be > 0"
        assert self.window_x_max > 0, "window_x_max must be > 0"
        assert self.predict_interval_s > 0, "predict_interval_s must be > 0"
        assert self.gate_min_evidence >= 1, "gate_min_evidence must be >= 1"
        assert self.gate_max_retries >= 0, "gate_max_retries must be >= 0"
        assert self.step_cap >= 1, "step_cap must be >= 1"
        assert self.tool_call_cap >= 1, "tool_call_cap must be >= 1"
        assert 0 < self.exec_timeout_s <= self.exec_max_timeout_s, \
            "exec_timeout_s must be in (0, exec_max_timeout_s]"
        assert self.exec_output_cap > 0, "exec_output_cap must be > 0"


_FIELD_NAMES = frozenset(f.name for f in dataclasses.fields(Config))
_YAML_FIELDS = _FIELD_NAMES - _SECRET_FIELDS


def load(path=None):
    """Return a typed Config: dataclass defaults <- config.yaml <- env secrets."""
    path = path or DEFAULT_YAML
    _load_dotenv()

    overrides = {}
    if os.path.exists(path):
        import yaml
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        assert isinstance(data, dict), f"{path}: top level must be a mapping"
        unknown = set(data) - _YAML_FIELDS
        assert not unknown, f"{path}: unknown/forbidden keys {sorted(unknown)} " \
            f"(secrets belong in .env, not the committed YAML)"
        overrides.update(data)

    # secrets come from env only
    for fld, env in _SECRET_ENV.items():
        val = os.environ.get(env)
        if val:
            overrides[fld] = val

    return Config(**overrides)


def _load_dotenv():
    """Load copilot/.env (preferred) or repo-root .env into os.environ if present.

    ponytail: python-dotenv is already installed; no hand-rolled parser.
    Existing env vars win over the file (real env overrides the dev file).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for cand in (os.path.join(HERE, ".env"), os.path.join(os.path.dirname(HERE), ".env")):
        if os.path.exists(cand):
            load_dotenv(cand, override=False)


def _selfcheck():
    # 1. code defaults ARE the documented F0 defaults
    d = Config()
    assert d.llm_profile == "nim" and d.embed_profile == "nim"
    assert d.emulate_pa is True and d.kg_enabled is True and d.ledger_to_kb is True
    assert d.history_compaction is False
    assert d.window_x_min == 10 and d.window_x_max == 60 and d.gate_min_evidence == 2
    assert d.gate_max_retries == 2 and d.error_profile == "light"
    assert d.step_cap == 8 and d.tool_call_cap == 6
    assert d.predict_interval_s == 3
    assert d.drift_distrust_at == "R3"
    assert d.exec_timeout_s == 30 and d.exec_max_timeout_s == 300
    assert d.exec_output_cap == 65536
    assert d.llm_api_key == "" and d.embed_api_key == ""

    # 2. shipped config.yaml must not drift from the code defaults
    loaded = load()
    for f in dataclasses.fields(Config):
        if f.name in _SECRET_FIELDS:
            continue
        assert getattr(loaded, f.name) == getattr(d, f.name), \
            f"config.yaml drifted from default on {f.name}: " \
            f"{getattr(loaded, f.name)!r} != {getattr(d, f.name)!r}"

    # 3. secrets load from env, never from yaml (restore any pre-existing value)
    saved = os.environ.get("COPILOT_LLM_API_KEY")
    os.environ["COPILOT_LLM_API_KEY"] = "sk-test"
    try:
        assert load().llm_api_key == "sk-test"
    finally:
        if saved is None:
            os.environ.pop("COPILOT_LLM_API_KEY", None)
        else:
            os.environ["COPILOT_LLM_API_KEY"] = saved

    # 4. validation bites on a bad enum / range
    for bad in (dict(llm_profile="gpt4"), dict(error_profile="perfect"),
                dict(window_x_min=0), dict(window_x_max=0), dict(gate_max_retries=-1), dict(step_cap=0),
                dict(tool_call_cap=0), dict(drift_distrust_at="R9"),
                dict(exec_timeout_s=0), dict(exec_timeout_s=301), dict(exec_output_cap=0)):
        try:
            Config(**bad)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"expected rejection for {bad}")

    print("copilot.config self-check OK")


if __name__ == "__main__":
    _selfcheck()
