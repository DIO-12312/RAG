import type {
  CreateDatasetRequest,
  DatasetDetail,
  DatasetSummary,
  Job,
} from "./contracts";
import { request } from "./http";

export function listDatasets(): Promise<DatasetSummary[]> {
  return request<DatasetSummary[]>("/datasets");
}

export function getDataset(datasetId: string): Promise<DatasetDetail> {
  return request<DatasetDetail>(`/datasets/${datasetId}`);
}

export function createDataset(payload: CreateDatasetRequest, key = crypto.randomUUID()): Promise<DatasetSummary> {
  return request<DatasetSummary>("/datasets", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });
}

export function uploadDocument(datasetId: string, file: File, key: string = crypto.randomUUID()): Promise<Job> {
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
