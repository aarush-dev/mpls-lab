// Single source of truth for the metrics the simulator emits and how HttpDataClient queries them.
// Each descriptor carries the Prometheus __name__, a PromQL template (with a `$dev` placeholder
// substituted for the queried device id), display metadata, the provenance `source`, and the
// `group` label the UI buckets series under. COUNTERS are wrapped in rate() here; GAUGES are raw.
//
// Not every metric exists on every device (P vs PE vs CE differ). A query that returns an empty
// result is normal — HttpDataClient simply yields no series for it.

import type { MetricDescriptor } from './types';

/** device= selector helper keeps the templates terse and consistent. */
const sel = (name: string) => `${name}{device="$dev"}`;
const rate2m = (name: string) => `rate(${sel(name)}[2m])`;

export const METRIC_CATALOG: MetricDescriptor[] = [
  // Interface throughput (counters -> rate, bytes/s).
  { name: 'interface_ifHCInOctets', promql: rate2m('interface_ifHCInOctets'), label: 'Interface RX', unit: 'bytes/s', source: 'measured', group: 'Interface throughput', entityLabel: 'interface' },
  { name: 'interface_ifHCOutOctets', promql: rate2m('interface_ifHCOutOctets'), label: 'Interface TX', unit: 'bytes/s', source: 'measured', group: 'Interface throughput', entityLabel: 'interface' },

  // Interface errors / discards (counters -> rate).
  { name: 'interface_ifInErrors', promql: rate2m('interface_ifInErrors'), label: 'Interface RX errors', unit: '1/s', source: 'measured', group: 'Interface errors', entityLabel: 'interface' },
  { name: 'interface_ifOutErrors', promql: rate2m('interface_ifOutErrors'), label: 'Interface TX errors', unit: '1/s', source: 'measured', group: 'Interface errors', entityLabel: 'interface' },
  { name: 'interface_ifInDiscards', promql: rate2m('interface_ifInDiscards'), label: 'Interface RX discards', unit: '1/s', source: 'measured', group: 'Interface errors', entityLabel: 'interface' },
  { name: 'interface_ifOutDiscards', promql: rate2m('interface_ifOutDiscards'), label: 'Interface TX discards', unit: '1/s', source: 'measured', group: 'Interface errors', entityLabel: 'interface' },

  // Interface status (gauge).
  { name: 'interface_ifOperStatus', promql: sel('interface_ifOperStatus'), label: 'Interface oper status', source: 'measured', group: 'Interface status', entityLabel: 'interface' },

  // Queue.
  { name: 'iface_queue_backlog_bytes', promql: sel('iface_queue_backlog_bytes'), label: 'Queue backlog', unit: 'bytes', source: 'measured', group: 'Queue', entityLabel: 'interface' },
  { name: 'iface_queue_drops', promql: rate2m('iface_queue_drops'), label: 'Queue drops', unit: '1/s', source: 'measured', group: 'Queue', entityLabel: 'interface' },

  // Device health.
  { name: 'node_cpu_pct', promql: sel('node_cpu_pct'), label: 'CPU', unit: '%', source: 'measured', group: 'Host CPU / memory' },
  { name: 'node_mem_pct', promql: sel('node_mem_pct'), label: 'Memory', unit: '%', source: 'measured', group: 'Host CPU / memory' },

  // BGP control-plane.
  { name: 'bgp_msg_rx_total', promql: rate2m('bgp_msg_rx_total'), label: 'BGP messages RX', unit: '1/s', source: 'measured', group: 'BGP control-plane' },
  { name: 'bgp_msg_tx_total', promql: rate2m('bgp_msg_tx_total'), label: 'BGP messages TX', unit: '1/s', source: 'measured', group: 'BGP control-plane' },
  { name: 'bgp_peer_established', promql: sel('bgp_peer_established'), label: 'BGP peers established', source: 'measured', group: 'BGP control-plane' },
  { name: 'bgp_vrf_prefix_count', promql: sel('bgp_vrf_prefix_count'), label: 'BGP VRF prefixes', source: 'measured', group: 'BGP control-plane', entityLabel: 'vrf' },

  // Routing (OSPF / RIB).
  { name: 'rib_routes', promql: sel('rib_routes'), label: 'RIB routes', source: 'measured', group: 'OSPF / RIB' },
  { name: 'ospf_lsa_count', promql: sel('ospf_lsa_count'), label: 'OSPF LSAs', source: 'measured', group: 'OSPF / RIB' },
  { name: 'ospf_neighbor_state', promql: sel('ospf_neighbor_state'), label: 'OSPF neighbor state', source: 'measured', group: 'OSPF / RIB' },
  { name: 'ospf_spf_last_duration_ms', promql: sel('ospf_spf_last_duration_ms'), label: 'OSPF SPF duration', unit: 'ms', source: 'measured', group: 'OSPF / RIB' },

  // MPLS.
  { name: 'mpls_lsp_count', promql: sel('mpls_lsp_count'), label: 'MPLS LSPs', source: 'measured', group: 'MPLS' },
  { name: 'mpls_ldp_session_state', promql: sel('mpls_ldp_session_state'), label: 'LDP session state', source: 'measured', group: 'MPLS' },

  // Tunnel (CE sites; entity = tunnel).
  { name: 'sdwan_tunnel_latency_ms', promql: sel('sdwan_tunnel_latency_ms'), label: 'Tunnel latency', unit: 'ms', source: 'simulated', group: 'Tunnel', entityLabel: 'tunnel' },
  { name: 'sdwan_tunnel_jitter_ms', promql: sel('sdwan_tunnel_jitter_ms'), label: 'Tunnel jitter', unit: 'ms', source: 'simulated', group: 'Tunnel', entityLabel: 'tunnel' },
  { name: 'sdwan_tunnel_loss_pct', promql: sel('sdwan_tunnel_loss_pct'), label: 'Tunnel loss', unit: '%', source: 'simulated', group: 'Tunnel', entityLabel: 'tunnel' },
  { name: 'sdwan_tunnel_rekeys_total', promql: `rate(${sel('sdwan_tunnel_rekeys_total')}[5m])`, label: 'Tunnel rekeys', unit: '1/s', source: 'simulated', group: 'Tunnel', entityLabel: 'tunnel' },

  // Chassis (modelled).
  { name: 'device_temp_c', promql: sel('device_temp_c'), label: 'Chassis temperature', unit: 'C', source: 'modelled', group: 'Chassis (modelled)' },
  { name: 'device_power_watts', promql: sel('device_power_watts'), label: 'Chassis power', unit: 'W', source: 'modelled', group: 'Chassis (modelled)' },
  { name: 'device_fan_rpm', promql: sel('device_fan_rpm'), label: 'Fan speed', unit: 'rpm', source: 'modelled', group: 'Chassis (modelled)' },
  { name: 'device_psu_voltage_v', promql: sel('device_psu_voltage_v'), label: 'PSU voltage', unit: 'V', source: 'modelled', group: 'Chassis (modelled)' },

  // Transceiver (modelled).
  { name: 'xcvr_temp_c', promql: sel('xcvr_temp_c'), label: 'Transceiver temperature', unit: 'C', source: 'modelled', group: 'Transceiver (modelled)', entityLabel: 'interface' },
  { name: 'xcvr_rx_power_dbm', promql: sel('xcvr_rx_power_dbm'), label: 'Transceiver RX power', unit: 'dBm', source: 'modelled', group: 'Transceiver (modelled)', entityLabel: 'interface' },
  { name: 'xcvr_tx_bias_ma', promql: sel('xcvr_tx_bias_ma'), label: 'Transceiver TX bias', unit: 'mA', source: 'modelled', group: 'Transceiver (modelled)', entityLabel: 'interface' },
];

const BY_NAME = new Map(METRIC_CATALOG.map((m) => [m.name, m]));

/** Descriptor for a Prometheus __name__, or undefined if not catalogued. */
export function metricInfoForName(name: string): MetricDescriptor | undefined {
  return BY_NAME.get(name);
}

/**
 * Group name for a MetricSeries key of the form `device[:entity]:name` — the last colon segment is
 * the metric __name__. Falls back to 'Other' for uncatalogued keys.
 */
export function catalogGroupFor(key: string): string {
  const name = key.slice(key.lastIndexOf(':') + 1);
  return BY_NAME.get(name)?.group ?? 'Other';
}

/** Ordered, de-duplicated list of catalog group names (for building group sections). */
export const METRIC_GROUPS: string[] = METRIC_CATALOG.reduce<string[]>((acc, m) => {
  if (!acc.includes(m.group)) {
    acc.push(m.group);
  }
  return acc;
}, []);
