import type {
  CreateDatasetRequest,
  DatasetDetail,
  DatasetSummary,
  DeleteDatasetResult,
  Job,
  SourceTopic,
} from "./contracts";
import { request } from "./http";
import { randomUUID } from "../utils/id";

export function listDatasets(): Promise<DatasetSummary[]> {
  return request<DatasetSummary[]>("/datasets");
}

export function getDataset(datasetId: string): Promise<DatasetDetail> {
  return request<DatasetDetail>(`/datasets/${datasetId}`);
}

export function createDataset(payload: CreateDatasetRequest, key = randomUUID()): Promise<DatasetSummary> {
  return request<DatasetSummary>("/datasets", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });
}

export function deleteDataset(datasetId: string, key = randomUUID()): Promise<DeleteDatasetResult> {
  return request<DeleteDatasetResult>(`/datasets/${datasetId}`, {
    method: "DELETE",
    headers: { "Idempotency-Key": key },
  });
}

export function uploadDocument(datasetId: string, file: File, key: string = randomUUID()): Promise<Job> {
  const data = new FormData(); data.append("file", file);
  return request<Job>(`/datasets/${datasetId}/documents`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: data,
  });
}

export function listDatasetJobs(datasetId: string): Promise<Job[]> {
  return request<Job[]>(`/datasets/${datasetId}/jobs`);
}

export function cancelJob(jobId: string): Promise<Job> {
  return request<Job>(`/jobs/${jobId}/cancel`, { method: "POST" });
}

export function retryJob(jobId: string): Promise<Job> {
  return request<Job>(`/jobs/${jobId}/retry`, { method: "POST" });
}

export function deleteDocument(documentId: string): Promise<void> {
  return request<void>(`/documents/${documentId}`, { method: "DELETE" });
}

export function getSourceTopic(
  documentId: string,
  indexVersion: number,
  topicPath: string,
  anchor?: string,
): Promise<SourceTopic> {
  const query = new URLSearchParams({ indexVersion: String(indexVersion), topicPath });
  if (anchor) query.set("anchor", anchor);
  return request<SourceTopic>(`/documents/${documentId}/source-topic?${query.toString()}`);
}
