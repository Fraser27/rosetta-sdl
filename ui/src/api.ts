import { getAccessToken, AUTH_ENABLED } from './auth';

const BASE = '/api';

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };

  // Add auth token if Cognito is enabled
  if (AUTH_ENABLED) {
    const token = getAccessToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
  }

  const res = await fetch(`${BASE}${path}`, {
    headers,
    ...opts,
  });

  if (res.status === 401) {
    // Token expired — redirect to login
    if (AUTH_ENABLED) {
      localStorage.clear();
      window.location.href = '/';
    }
    throw new Error('Unauthorized');
  }

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json();
}

// Types
export interface TableSummary {
  full_name: string;
  name: string;
  database: string;
  description: string;
  catalog_type: string;
  datasource: string;
}

export interface Column {
  name: string;
  data_type: string;
  description: string;
  is_partition: boolean;
  is_primary_key: boolean;
}

export interface TableDetail {
  full_name: string;
  name: string;
  database: string;
  description: string;
  catalog_type: string;
  columns: Column[];
  joins: { related_table: string; on_column: string; join_type: string }[];
}

export interface Metric {
  metric_id: string;
  name: string;
  definition: string;
  expression: string;
  type: string;
  source_table: string;
  synonyms: string[] | null;
  grain: string[] | null;
  filters?: string[];
  time_grains?: string[];
  owner?: string;
}

export interface GraphSummary {
  nodes: Record<string, number>;
  edges: Record<string, number>;
}

export interface SearchResult {
  type: string;
  id: string;
  name: string;
  description: string;
  score: number;
}

// API calls
export const api = {
  health: () => request<{ status: string; neo4j: string }>('/health'),

  // Catalog
  listTables: () => request<TableSummary[]>('/catalog/tables'),
  getTable: (name: string) => request<TableDetail>(`/catalog/tables/${name}`),
  search: (q: string) => request<SearchResult[]>(`/catalog/search?q=${encodeURIComponent(q)}`),
  graphSummary: () => request<GraphSummary>('/catalog/graph'),

  // Metrics
  listMetrics: () => request<Metric[]>('/metrics'),
  getMetric: (id: string) => request<Metric>(`/metrics/${id}`),
  createMetric: (m: Partial<Metric>) =>
    request<Metric>('/metrics', { method: 'POST', body: JSON.stringify(m) }),
  updateMetric: (id: string, m: Partial<Metric>) =>
    request<Metric>(`/metrics/${id}`, { method: 'PUT', body: JSON.stringify(m) }),
  deleteMetric: (id: string) =>
    request<{ ok: boolean }>(`/metrics/${id}`, { method: 'DELETE' }),

  // Admin
  scan: () => request<Record<string, unknown>>('/admin/scan', { method: 'POST' }),
  enrich: () => request<Record<string, unknown>>('/admin/enrich', { method: 'POST' }),
  clear: () => request<Record<string, unknown>>('/admin/clear', { method: 'POST' }),

  // Graph data for visualization
  graphData: () => request<{ nodes: GraphNode[]; edges: GraphEdge[] }>('/catalog/graph/data'),
};

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
}
