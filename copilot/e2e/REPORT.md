# E1 end-to-end run -- manual E2E record (#42)

Model `openai/gpt-oss-20b` (effort=high) over `https://integrate.api.nvidia.com/v1`; live dataapi + real seeded KB.

Verdict legend: **cited answer** = answered with valid `[source:id]` citations; **gated** = gate withheld the answer and returned what's-missing (both are acceptance-valid); **ask-back** = asked one clarifying question.

| # | question | intended tools | tools called | verdict | gate fails | s |
|---|---|---|---|---|---|---|
| 1 | Is PE router pe6 healthy right now? Check its live metrics a | query_metrics | query_metrics,search_logs,search_logs,query_metrics,search_logs | gated (what's-missing) | 3 | 49.1 |
| 2 | Search pe8's logs for any BGP or OSPF error or adjacency-cha | search_logs | search_logs,search_logs | answer (uncited) | 1 | 40.7 |
| 3 | What traffic flows are moving through ce_branch16 right now, | flows | flows | cited answer | 0 | 72.2 |
| 4 | If pe6 is degrading, which downstream devices are in its bla | walk_topology_graph | walk_topology_graph | cited answer | 1 | 124.4 |
| 5 | Find the runbook for handling a BGP adjacency that has gone  | search_runbooks | search_runbooks | cited answer | 0 | 10.1 |
| 6 | Have we seen tunnel degradation before? Show similar past in | search_incidents | search_incidents,search_logs,search_incidents,search_logs,search_runbooks,search_incidents | stopped:step_cap | 2 | 62.7 |
| 7 | Is the network ok? | - | - | ask-back | 0 | 1.4 |

## Per-question detail

### 1. metrics-health
- **Q:** Is PE router pe6 healthy right now? Check its live metrics and cite them.
- **#40 landmine ridden:** PromQL synth on /metrics; per-series latest sample
- **tools called:** query_metrics, search_logs, search_logs, query_metrics, search_logs
- **verdict:** gated (what's-missing)  (gate fails: 3, stopped: None)
- **answer:**

  > cannot answer yet: self-judge: Evidence indicating that there is no BGP flapping on pe6 (e.g., log entries containing 'bgp_flap' or a metric such as 'bgp_fsm_flapped'), or a clear metric that confirms all BGP peers remain stable.

- trace: `copilot/e2e/traces/metrics-health.json`

### 2. logs-errors
- **Q:** Search pe8's logs for any BGP or OSPF error or adjacency-change messages in the window.
- **#40 landmine ridden:** /events pattern+offset done adapter-side; ISO ts->epoch
- **tools called:** search_logs, search_logs
- **verdict:** answer (uncited)  (gate fails: 1, stopped: None)
- **answer:**

  > Could you please specify the date‑time range (or the exact “investigation window”) you’d like me to search in PE8’s logs?

- trace: `copilot/e2e/traces/logs-errors.json`

### 3. flows-volume
- **Q:** What traffic flows are moving through ce_branch16 right now, and how big are they?
- **#40 landmine ridden:** /flows approximate log-time windowing; stamp_updated ts->epoch
- **tools called:** flows
- **verdict:** cited answer  (gate fails: 0, stopped: None)
- **answer:**

  > On **ce_branch16** there are two currently active IPv6‑ICMP flows: one packet (56 bytes) originating from **fe80::42:acff:fe14:14cb** and directed to the all‑nodes multicast address *ff02::2*, and a second packet (56 bytes) from **fe80::42:acff:fe14:14df** to the same multicast address. Each flow consists of a single packet. [flows:0] [flows:1]

- trace: `copilot/e2e/traces/flows-volume.json`

### 4. topology-blast
- **Q:** If pe6 is degrading, which downstream devices are in its blast radius and what is their live status?
- **#40 landmine ridden:** BFS + /metrics enrich over real topology
- **tools called:** walk_topology_graph
- **verdict:** cited answer  (gate fails: 1, stopped: None)
- **answer:**

  > The one‑hop downstream devices from **pe6 [topo:pe6]** are **ce_branch18 [topo:ce_branch18]**, **ce_branch6 [topo:ce_branch6]**, **ce_hub6 [topo:ce_hub6]**, **p11 [topo:p11]**, and **p12 [topo:p12]** – all of them show **interface_ifOperStatus = 1** in their metrics, indicating live status.  
  > 
  > The two‑hop downstream devices include **p10 [topo:p10]**, **p9 [topo:p9]**, and **pe5 [topo:pe5]** – each also reports **interface_ifOperStatus = 1**, confirming they are live.  
  > 
  > The remaining **h_branch*** nodes – **h_branch18_corp [topo:h_branch18_corp]**, **h_branch18_voice [topo:h_branch18_voice]**, **h_branch6_corp [topo:h_branch6_corp]**, **h_branch6_voice [topo:h_branch6_voice]**, **h_hub6_corp [topo:h_hub6_corp]**, **h_hub6_guest [topo:h_hub6_guest]**, **h_hub6_voice [topo:h_hub6_voice]** – have **no metrics** available in the current telemetry window, so their live status cannot be confirmed.

- trace: `copilot/e2e/traces/topology-blast.json`

### 5. kb-runbook
- **Q:** Find the runbook for handling a BGP adjacency that has gone down.
- **#40 landmine ridden:** real nv-embedqa retrieval over seeded runbooks
- **tools called:** search_runbooks
- **verdict:** cited answer  (gate fails: 0, stopped: None)
- **answer:**

  > The relevant runbook is **`runbook-bgp-adjacency-down`** [runbook-bgp-adjacency-down].

- trace: `copilot/e2e/traces/kb-runbook.json`

### 6. kb-incident
- **Q:** Have we seen tunnel degradation before? Show similar past incidents near ce_branch1.
- **#40 landmine ridden:** retrieval + topology-hop proximity filter
- **tools called:** search_incidents, search_logs, search_incidents, search_logs, search_runbooks, search_incidents
- **verdict:** stopped:step_cap  (gate fails: 2, stopped: step_cap)
- **answer:**

  > stopped: step_cap reached before a conclusion

- trace: `copilot/e2e/traces/kb-incident.json`

### 7. ask-back-vague
- **Q:** Is the network ok?
- **#40 landmine ridden:** R1 ask-back: a vague no-device question must ask one question, not fabricate
- **tools called:** none
- **verdict:** ask-back  (gate fails: 0, stopped: None)
- **answer:**

  > Could you specify which part of the network (e.g., a specific device, VLAN, or service) and time period you’d like me to check?

- trace: `copilot/e2e/traces/ask-back-vague.json`

