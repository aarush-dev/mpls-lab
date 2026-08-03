"""copilot.emulator -- Lane-Runtime (R4a). PA-emulator: /labels ground truth -> §3.3 Prediction Record.

R4a (ADR-0003): the ONLY seam between copilot and the prediction stack is the Prediction Record
(PA.md §3.3). `emulate_record` derives a full-fidelity record from a ground-truth fault label;
`prediction` is the `emulate_pa`-routed seam (emulator vs real PA, no caller change); `persist`
lands a record in the Event Ledger (ADR-0009). `fault_type`/`is_abstain` are the two consumer
accessors -- skill selection (ADR-0012) + gate softening (ADR-0008). The periodic firing that
writes records every ~predict_interval_s is R4b (ADR-0014).
"""
from copilot.emulator.emulate import (
    emulate_record, family, fault_type, fetch_labels, is_abstain, persist, prediction, to_wire,
)
from copilot.emulator.predictor import predict_once, run_predictor

__all__ = [
    "emulate_record", "family", "fault_type", "fetch_labels", "is_abstain", "persist",
    "predict_once", "prediction", "run_predictor", "to_wire",
]
