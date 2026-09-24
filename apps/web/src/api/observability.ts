import { request } from "./http";

export type Window = "15m" | "1h" | "6h" | "24h";
export type Service = "rag-go-api" | "rag-python-server" | "rag-python-worker" | "rag-python-outbox";
export type DataStatus = "ok" | "empty" | "partial" | "unavailable";

export interface Point { time: number; value: number }
export interface Series { name: string; points: Point[] }
export interface Panel { key: string; status: DataStatus; code?: string; series: Series[] }
export interface MetricsResponse { status: DataStatus; window: Window; panels: Panel[] }
export interface TraceSummary { traceId: string; service: Service; startedAt: string; durationMs: number }
export interface TraceList { status: DataStatus; service: Service; window: Window; traces: TraceSummary[] }
export interface Span {
  spanId: string;
  parentSpanId?: string;
  service: Service;
  stage: string;
  startedAt: string;
  durationMs: number;
  outcome: string;
  errorCode?: string;
  runId?: string;
  jobId?: string;
  taskId?: string;
}
export interface TraceDetail { status: DataStatus; traceId: string; spans: Span[]; truncated: boolean }

export function getMetrics(window: Window): Promise<MetricsResponse> {
  return request<MetricsResponse>(`/admin/observability/metrics?window=${window}`);
}
export function getTraces(service: Service, window: Window): Promise<TraceList> {
  return request<TraceList>(`/admin/observability/traces?service=${service}&window=${window}`);
}
export function getTrace(traceId: string): Promise<TraceDetail> {
  return request<TraceDetail>(`/admin/observability/traces/${encodeURIComponent(traceId)}`);
}
