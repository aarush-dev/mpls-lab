// Pure target-selection helpers for the Fault Injection tab. No React, no I/O — so the role
// filter + POP synthesis are unit-testable in isolation (issue #88).
//
// Roles mirror the backend exactly (dataapi/faults_api.py:_role_of, dataapi/sources.py:_role_of):
// ce_branch | ce_hub | ce_dc | pe | pop | srlg | p | unknown. A scenario's `valid_roles`
// (GET /faults/scenarios) is the ground truth for which targets it accepts.

import type { TopologyNode } from './types';

export interface TargetOption {
  label: string;
  value: string;
}

/** Coarse role of a target id — mirrors the backend so the client can pre-check before POST. */
export function roleOf(target: string): string {
  const t = String(target);
  if (t.startsWith('ce_branch')) {
    return 'ce_branch';
  }
  if (t.startsWith('ce_hub')) {
    return 'ce_hub';
  }
  if (t.startsWith('ce_dc')) {
    return 'ce_dc';
  }
  if (t.startsWith('pe')) {
    return 'pe';
  }
  if (t.startsWith('pop')) {
    return 'pop';
  }
  if (t.startsWith('srlg')) {
    return 'srlg';
  }
  if (t.startsWith('p')) {
    return 'p';
  }
  return 'unknown';
}

/** Client-side role pre-check. Sub-role constraints (ABR/RR-only) aren't visible here — the
 *  backend 422 is the backstop for those. */
export function isValidTarget(target: string, validRoles: string[]): boolean {
  return validRoles.includes(roleOf(target));
}

/**
 * Target options for a scenario, given its valid_roles + live topology nodes.
 * - device roles (ce_*/pe/p) -> the nodes carrying that role.
 * - 'pop' -> synthesized from the distinct derived node POPs (pop1, pop2, …).
 * - 'srlg' contributes no concrete option (custom-value long-tail; caller's allowCustomValue).
 * Sorted by value for a stable list.
 */
export function targetOptions(validRoles: string[], nodes: TopologyNode[]): TargetOption[] {
  const values = new Set<string>();
  const roles = new Set(validRoles);
  for (const n of nodes) {
    if (roles.has(n.role)) {
      values.add(n.id);
    }
  }
  if (roles.has('pop')) {
    for (const n of nodes) {
      if (n.pop) {
        values.add(n.pop);
      }
    }
  }
  return [...values]
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
    .map((v) => ({ label: `${v} (${roleOf(v)})`, value: v }));
}
