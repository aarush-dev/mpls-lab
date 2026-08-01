import { MockDataClient, MOCK_WINDOW_BUCKETS, MOCK_BUCKET_META, TopologyNodeLive } from './MockDataClient';

describe('MockDataClient', () => {
  it('getTopology({}) returns 148 nodes and 361 links with valid states', async () => {
    const client = new MockDataClient();
    const { nodes, links } = await client.getTopology({});
    expect(nodes).toHaveLength(148);
    expect(links).toHaveLength(361);
    for (const n of nodes as TopologyNodeLive[]) {
      expect(['red', 'amber', 'green']).toContain(n.state);
    }
  });

  it('setCursor drives node state: amber at bucket 2, green at calm bucket 0', async () => {
    const client = new MockDataClient();

    client.setCursor(2);
    const degraded = await client.getTopology({});
    const amberNode = degraded.nodes.find((n) => n.id === 'ce_branch18') as TopologyNodeLive;
    expect(amberNode.state).toBe('amber');

    client.setCursor(0);
    const calm = await client.getTopology({});
    const calmNode = calm.nodes.find((n) => n.id === 'ce_branch18') as TopologyNodeLive;
    expect(calmNode.state).toBe('green');
  });

  it('getTopology({pop}) returns only nodes for that pop', async () => {
    const client = new MockDataClient();
    const { nodes } = await client.getTopology({ pop: 'pop1' });
    expect(nodes.length).toBeGreaterThan(0);
    for (const n of nodes) {
      expect(n.pop).toBe('pop1');
    }
  });

  it('getPredictions({device}) returns only that device, all mock-sourced', async () => {
    const client = new MockDataClient();
    client.setCursor(3); // inside pred-inc-congestion-ce_branch18-fc10a128-0's active window [2,4)
    const predictions = await client.getPredictions({ device: 'ce_branch18' });
    expect(predictions.length).toBeGreaterThan(0);
    for (const p of predictions) {
      expect(p.deviceId).toBe('ce_branch18');
      expect(p.source).toBe('mock');
    }
  });

  it('getIncidents reflects cursor-derived status', async () => {
    const client = new MockDataClient();

    // inc-congestion-ce_branch18-fc10a128: startBucket 2, impactBucket 4, endBucket 4
    client.setCursor(3);
    const inside = await client.getIncidents({});
    const inc = inside.find((i) => i.id === 'inc-congestion-ce_branch18-fc10a128');
    expect(inc).toBeDefined();
    expect(['active', 'open']).toContain(inc!.status);

    client.setCursor(1); // before startBucket
    const before = await client.getIncidents({});
    expect(before.find((i) => i.id === 'inc-congestion-ce_branch18-fc10a128')).toBeUndefined();
  });

  it('getCapabilities returns a valid dataset window and non-empty sources', async () => {
    const client = new MockDataClient();
    const caps = await client.getCapabilities();
    expect(caps.datasetWindow.fromMs).toBeLessThan(caps.datasetWindow.toMs);
    expect(Object.keys(caps.sources).length).toBeGreaterThan(0);
  });

  it('getTelemetry returns windowed series for any device; empty only when no deviceId given', async () => {
    const client = new MockDataClient();
    const series = await client.getTelemetry({ deviceId: 'ce_branch1' });
    expect(series.length).toBeGreaterThan(0);
    for (const s of series) {
      expect(s.points).toHaveLength(MOCK_WINDOW_BUCKETS);
    }

    // Every selectable device is now filled (real or deterministic synth) — never an empty page.
    const synth = await client.getTelemetry({ deviceId: 'does_not_exist' });
    expect(synth.length).toBeGreaterThan(0);
    // No deviceId at all is still an empty request.
    expect(await client.getTelemetry({})).toEqual([]);
  });

  it('every topology node yields at least one series with real values (all charts filled)', async () => {
    const client = new MockDataClient();
    const { nodes } = await client.getTopology({});
    // Sample one of each role so the test stays fast but covers host + core + edge.
    const sample = ['host', 'p', 'pe', 'ce_hub', 'ce_branch', 'ce_dc'].map(
      (role) => nodes.find((n) => n.role === role)?.id
    );
    for (const id of sample) {
      expect(id).toBeTruthy();
      const series = await client.getTelemetry({ deviceId: id! });
      expect(series.length).toBeGreaterThan(0);
      expect(series.some((s) => s.points.some((p) => p.value != null))).toBe(true);
    }
  });

  it('fabricates the dead series: PE transceiver is non-null and error counters are non-zero', async () => {
    const client = new MockDataClient();
    const series = await client.getTelemetry({ deviceId: 'pe1' });
    const xcvr = series.filter((s) => s.key.includes('xcvr_'));
    expect(xcvr.length).toBeGreaterThan(0);
    expect(xcvr.some((s) => s.points.some((p) => p.value != null))).toBe(true);
    const errs = series.filter((s) => /if_(in|out)_errors|if_in_discards/.test(s.key));
    expect(errs.length).toBeGreaterThan(0);
    expect(errs.some((s) => s.points.some((p) => (p.value ?? 0) > 0))).toBe(true);
  });

  it('getActiveAlerts derives NodeDown (red now) and NodeDownPredicted (<=5min to impact) across the tape', () => {
    const client = new MockDataClient();
    const kinds = new Set<string>();
    for (let c = 0; c < MOCK_BUCKET_META.bucketCount; c++) {
      client.setCursor(c);
      for (const a of client.getActiveAlerts()) {
        kinds.add(a.alertname);
        expect(a.node).toBeTruthy();
        expect(['critical', 'warning']).toContain(a.severity);
        if (a.alertname === 'NodeDownPredicted') {
          expect(a.severity).toBe('warning');
          expect(a.summary).toMatch(/predicted .* in \d+s/);
        }
      }
    }
    expect(kinds.has('NodeDown')).toBe(true);
    expect(kinds.has('NodeDownPredicted')).toBe(true);
  });

  it('injected faults escalate by phase: predicted->amber+prediction alert, down->red+NodeDown', async () => {
    const client = new MockDataClient();
    client.setCursor(0);
    const setFaults = (client as unknown as {
      setInjectedFaults: (f: Array<{ node: string; faultType: string; phase: string; leadSec: number }>) => void;
    }).setInjectedFaults.bind(client);

    // Predicted phase: node reads amber and raises a T-minus prediction alert (not down yet).
    setFaults([{ node: 'pe1', faultType: 'congestion', phase: 'predicted', leadSec: 60 }]);
    let g = await client.getTopology({});
    expect((g.nodes as TopologyNodeLive[]).find((n) => n.id === 'pe1')?.state).toBe('amber');
    let alerts = client.getActiveAlerts();
    const pred = alerts.find((a) => a.node === 'pe1' && a.alertname === 'NodeDownPredicted');
    expect(pred).toBeDefined();
    expect(pred!.severity).toBe('warning');
    expect(pred!.summary).toContain('60s');
    expect(alerts.find((a) => a.node === 'pe1' && a.alertname === 'NodeDown')).toBeUndefined();

    // Down phase: node goes red and raises a critical NodeDown carrying the fault type.
    setFaults([{ node: 'pe1', faultType: 'congestion', phase: 'down', leadSec: 60 }]);
    g = await client.getTopology({});
    expect((g.nodes as TopologyNodeLive[]).find((n) => n.id === 'pe1')?.state).toBe('red');
    alerts = client.getActiveAlerts();
    const down = alerts.find((a) => a.node === 'pe1' && a.alertname === 'NodeDown');
    expect(down).toBeDefined();
    expect(down!.severity).toBe('critical');
    expect(down!.summary).toContain('congestion');

    // Clearing restores the node.
    setFaults([]);
    const after = await client.getTopology({});
    expect((after.nodes as TopologyNodeLive[]).find((n) => n.id === 'pe1')?.state).not.toBe('red');
  });

  it('adds a fault-predictor series to an injected node, climbing as the phase escalates', async () => {
    const client = new MockDataClient();
    client.setCursor(10);
    const setFaults = (client as unknown as {
      setInjectedFaults: (f: Array<{ node: string; faultType: string; phase: string; leadSec: number }>) => void;
    }).setInjectedFaults.bind(client);
    const predOf = async () => {
      const s = await client.getTelemetry({ deviceId: 'pe1' });
      return s.find((m) => m.key === 'pe1:predictor');
    };

    // No fault -> no predictor series.
    expect(await predOf()).toBeUndefined();

    // Predicted -> predictor present and trending up toward "now" (traffic fault = rising pressure).
    setFaults([{ node: 'pe1', faultType: 'congestion', phase: 'predicted', leadSec: 60 }]);
    const predicted = await predOf();
    expect(predicted).toBeDefined();
    const pts = predicted!.points;
    expect(pts[pts.length - 1].value!).toBeGreaterThan(pts[0].value!);

    // Down amplitude exceeds predicted amplitude (curve is steeper closer to impact).
    setFaults([{ node: 'pe1', faultType: 'congestion', phase: 'down', leadSec: 60 }]);
    const down = await predOf();
    expect(down!.points[down!.points.length - 1].value!).toBeGreaterThan(pts[pts.length - 1].value!);
  });

  it('rewrites chart timestamps to a monotonic timeline that never rewinds on loop', async () => {
    const client = new MockDataClient();
    const bucketCount = MOCK_BUCKET_META.bucketCount;

    // Near the end of the tape.
    client.setCursor(bucketCount - 1);
    client.setAbsTick(bucketCount - 1);
    const before = await client.getTelemetry({ deviceId: 'ce_branch1' });
    const lastBefore = before[0].points[before[0].points.length - 1].tMs;
    // Points are strictly increasing within the window.
    for (const s of before) {
      for (let i = 1; i < s.points.length; i++) {
        expect(s.points[i].tMs).toBeGreaterThan(s.points[i - 1].tMs);
      }
    }

    // Loop: data cursor wraps to 0 but absTick keeps climbing.
    client.setCursor(0);
    client.setAbsTick(bucketCount + 4);
    const after = await client.getTelemetry({ deviceId: 'ce_branch1' });
    const lastAfter = after[0].points[after[0].points.length - 1].tMs;
    expect(lastAfter).toBeGreaterThan(lastBefore); // no backward jump across the seam
  });

  it('sendMessage succeeds against a real incident context', async () => {
    const client = new MockDataClient();
    client.setCursor(3); // inside inc-congestion-ce_branch18-fc10a128's window
    const result = await client.sendMessage({
      conversationId: 't',
      message: { id: 'u1', role: 'user', content: '?', createdAt: 'x', state: 'sending' },
      context: { incidentId: 'inc-congestion-ce_branch18-fc10a128', deviceIds: ['ce_branch18'] },
    });
    expect(result.message.state).toBe('complete');
    expect(result.response).toBeDefined();
    expect(result.response!.citations.length).toBeGreaterThan(0);
    expect(result.response!.recommendedActions.length).toBeGreaterThan(0);
  });

  it('getConversation resolves for a real seed id, rejects for an unknown id', async () => {
    const client = new MockDataClient();
    const conv = await client.getConversation('conv-seed-congestion');
    expect(conv.id).toBe('conv-seed-congestion');

    await expect(client.getConversation('nope')).rejects.toBeTruthy();
  });
});
