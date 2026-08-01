import { buildAmAlerts, AlertDescriptor } from './alertPublisher';

describe('buildAmAlerts', () => {
  const origin = 'http://localhost:3000';

  it('maps a descriptor to Alertmanager labels/annotations with a source label and node link', () => {
    const d: AlertDescriptor = {
      alertname: 'NodeDown',
      node: 'pe1',
      severity: 'critical',
      pop: 'pop-1',
      summary: 'pe1 is down',
    };
    const [alert] = buildAmAlerts([d], origin);
    expect(alert.labels).toEqual({
      alertname: 'NodeDown',
      node: 'pe1',
      severity: 'critical',
      source: 'noc-copilot',
      pop: 'pop-1',
    });
    expect(alert.annotations).toEqual({ summary: 'pe1 is down' });
    expect(alert.generatorURL).toBe('http://localhost:3000/a/mplslab-noccopilot-app/node/pe1');
  });

  it('omits optional pop/description and does not send startsAt (AM stamps receive-time)', () => {
    const [alert] = buildAmAlerts(
      [{ alertname: 'NodeDownPredicted', node: 'ce_branch18', severity: 'warning', summary: 's' }],
      origin
    );
    expect(alert.labels.pop).toBeUndefined();
    expect(alert.annotations.description).toBeUndefined();
    expect('startsAt' in alert).toBe(false);
  });
});
