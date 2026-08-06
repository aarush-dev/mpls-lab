import { precursorDevices, PaAlertsResponse } from './paAlerts';

const resp = (alerts: Array<{ device: string; entity_id: string }>): PaAlertsResponse => ({
  ts: null,
  mode: 'rank',
  warm: true,
  alerts: alerts.map((a) => ({ ...a, cause: null, p_any: 0.9 })),
  predictions: [],
  n_scored: 10,
});

describe('precursorDevices', () => {
  it('collects the distinct devices from alerts (the blink set)', () => {
    const s = precursorDevices(
      resp([
        { device: 'pe1', entity_id: 'pe1:tun0' },
        { device: 'pe1', entity_id: 'pe1:tun1' }, // same device, two entities -> one node
        { device: 'ce_branch18', entity_id: 'ce_branch18' },
      ])
    );
    expect(s).toEqual(new Set(['pe1', 'ce_branch18']));
  });

  it('is empty when nothing is alerting', () => {
    expect(precursorDevices(resp([])).size).toBe(0);
  });
});
