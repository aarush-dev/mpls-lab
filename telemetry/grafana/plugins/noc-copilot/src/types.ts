export type Severity = 'CRITICAL' | 'WARNING' | 'INFO';
export type Health = 'critical' | 'warning' | 'ok';
export type NodeRole = 'P' | 'PE' | 'CE-Branch' | 'CE-Hub' | 'CE-DC';

export interface Alert {
  id: string;
  device: string;
  faultType: string;
  severity: Severity;
  tti: number;
  timestamp: string;
  confidence: number;
}

export interface NetworkNode {
  id: string;
  role: NodeRole;
  label: string;
  ip: string;
  health: Health;
  x: number;
  y: number;
  vrfs?: string[];
  warnings?: string[];
}

export interface NetworkLink {
  source: string;
  target: string;
  health: Health;
}

export interface FaultScenario {
  id: string;
  name: string;
  description: string;
  severity: Severity;
  target: string;
  icon: string;
}

export interface ChatMessage {
  role: 'user' | 'ai';
  content: string;
  showChart?: boolean;
}

export interface DiagnosisResult {
  device: string;
  faultType: string;
  confidence: number;
  rootCause: string;
  affectedScope: string[];
  vrfs: string[];
  tti: number;
  actions: string[];
  sources: string[];
}
