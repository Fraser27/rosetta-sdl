import { getAccessToken, isAuthEnabled } from './auth';

const BASE = '/api';

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };

  // Add auth token if Cognito is enabled
  if (isAuthEnabled()) {
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
    if (isAuthEnabled()) {
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

export interface MetricJoin {
  table: string;
  source_column: string;
  target_column: string;
  join_type: string;
}

export interface Metric {
  metric_id: string;
  name: string;
  definition: string;
  expression: string;
  type: string;
  source_table: string;
  joins: MetricJoin[];
  base_metrics: string[] | null;
  synonyms: string[] | null;
  grain: string[] | null;
  filters?: string[];
  time_grains?: string[];
  owner?: string;
  source?: string;
}

export interface DocumentSummary {
  s3_key: string;
  name: string;
  description: string;
  type: string;
  vector_bucket: string;
  vector_index: string;
  related_tables: string[];
  concepts: string[];
}

export interface DocumentDetail extends DocumentSummary {
  metadata_keys: { name: string; data_type: string; filterable: boolean }[];
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
  updateTableDescription: (name: string, description: string) =>
    request<{ ok: boolean }>(`/catalog/tables/${name}/description`, {
      method: 'PATCH', body: JSON.stringify({ description }),
    }),
  updateColumnDescription: (tableName: string, columnName: string, description: string) =>
    request<{ ok: boolean }>(`/catalog/tables/${tableName}/columns/${columnName}/description`, {
      method: 'PATCH', body: JSON.stringify({ description }),
    }),
  listDocuments: () => request<DocumentSummary[]>('/catalog/documents'),
  getDocument: (key: string) => request<DocumentDetail>(`/catalog/documents/${key}`),
  updateDocumentDescription: (key: string, description: string) =>
    request<{ ok: boolean }>(`/catalog/documents/${key}/description`, {
      method: 'PATCH', body: JSON.stringify({ description }),
    }),
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
  compileMetric: (id: string) =>
    request<{ metric: string; sql: string; source_table: string }>(`/metrics/${id}/compile`, { method: 'POST' }),
  composeMetrics: (metric_ids: string[], dimensions: string[], limit?: number) =>
    request<{ sql: string; metric: string; results?: unknown }>('/query/compose', {
      method: 'POST',
      body: JSON.stringify({ metric_ids, dimensions, limit }),
    }),
  executeComposed: (metric_ids: string[], dimensions: string[], limit?: number) =>
    request<{ sql: string; metric: string; results: unknown }>('/query/compose', {
      method: 'POST',
      body: JSON.stringify({ metric_ids, dimensions, limit, execute: true }),
    }),

  // Admin
  scan: () => request<Record<string, unknown>>('/admin/scan', { method: 'POST' }),
  enrich: (datasources: string[] = [], force = false, model_id = '') =>
    request<{ status: string; job_id: string }>('/admin/enrich', {
      method: 'POST',
      body: JSON.stringify({ datasources, force, model_id }),
    }),
  enrichStatus: (jobId: string) => request<EnrichmentJob>(`/admin/enrich/${jobId}`),
  listDatasources: () => request<{ name: string; table_count: number }[]>('/admin/datasources'),
  getConfig: () => request<Record<string, string>>('/admin/config'),
  sampleDataStatus: () => request<{ loaded: boolean; datasources: number; metrics: number }>('/admin/sample-data/status'),
  loadSampleData: () => request<Record<string, unknown>>('/admin/sample-data/load', { method: 'POST' }),
  deleteSampleData: () => request<Record<string, unknown>>('/admin/sample-data', { method: 'DELETE' }),
  clear: () => request<Record<string, unknown>>('/admin/clear', { method: 'POST' }),

  // Graph data for visualization
  graphData: () => request<{ nodes: GraphNode[]; edges: GraphEdge[] }>('/catalog/graph/data'),
};

export interface EnrichmentJob {
  job_id: string;
  status: string;
  datasources: string[];
  force: boolean;
  tables: { total: number; enriched: number; skipped: number; failed: number };
  documents: { total: number; enriched: number };
  current_table: string;
  elapsed_seconds?: number;
  error?: string;
}

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  datasource: string | null;
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
}
