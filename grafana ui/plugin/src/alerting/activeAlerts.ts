// Derives the current firing alert set from the client-side injected faults (the Fault Injection
// tab's instant-feedback overlay). In api mode the data client has no getActiveAlerts(); injected
// faults are the demo's alert source. Real backend faults surface separately via /labels -> incidents.
// Pure function (no Date/fetch) so it stays testable and App.tsx owns the side effects.
import type { InjectedFault } from '../state/reducer';
import type { AlertDescriptor } from './alertPublisher';

/** injected faults -> Alertmanager descriptors. 'down' => critical NodeDown, 'predicted' => warning
 *  NodeDownPredicted, 'pending' => no alert (still in the healthy grace window). */
export function activeAlertsFromInjected(injected: InjectedFault[]): AlertDescriptor[] {
  const out: AlertDescriptor[] = [];
  for (const f of injected) {
    if (f.phase === 'down') {
      out.push({
        alertname: 'NodeDown',
        node: f.node,
        severity: 'critical',
        summary: `${f.node} is down`,
        description: `Injected ${f.faultType}: node ${f.node} is DOWN.`,
      });
    } else if (f.phase === 'predicted') {
      out.push({
        alertname: 'NodeDownPredicted',
        node: f.node,
        severity: 'warning',
        summary: `${f.node} predicted to fail`,
        description: `Predicted ${f.faultType} on ${f.node} within ~${f.leadSec}s.`,
      });
    }
  }
  return out;
}
