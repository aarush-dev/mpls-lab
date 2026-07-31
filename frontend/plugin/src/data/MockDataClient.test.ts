import { MockDataClient, MOCK_WINDOW_BUCKETS, TopologyNodeLive } from './MockDataClient';

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

  it('getTelemetry returns windowed series for a known device, empty for unknown/missing', async () => {
    const client = new MockDataClient();
    const series = await client.getTelemetry({ deviceId: 'ce_branch1' });
    expect(series.length).toBeGreaterThan(0);
    for (const s of series) {
      expect(s.points).toHaveLength(MOCK_WINDOW_BUCKETS);
    }

    expect(await client.getTelemetry({ deviceId: 'does_not_exist' })).toEqual([]);
    expect(await client.getTelemetry({})).toEqual([]);
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
