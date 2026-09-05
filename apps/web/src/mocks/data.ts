import type { DatasetDetail, DatasetSummary, Job, SettingsResponse } from "@/api/contracts";

const initialDatasets: DatasetDetail[] = [
  {
    id: "dataset-ready",
    name: "产品发布资料",
    status: "READY",
    documentCount: 3,
    updatedAt: "2026-09-05T09:30:00Z",
    documents: [{ id: "document-release-notes", name: "release-notes.pdf", status: "INDEXED" }],
  },
  {
    id: "dataset-processing",
    name: "架构设计草案",
    status: "PROCESSING",
    documentCount: 1,
    updatedAt: "2026-09-05T09:10:00Z",
    documents: [{ id: "document-architecture", name: "architecture.md", status: "PROCESSING" }],
  },
];

const initialJobs: Job[] = [
  {
    id: "job-failed-retryable",
    datasetId: "dataset-ready",
    sourceName: "legacy-import.docx",
    status: "FAILED",
    progress: 62,
    retryable: true,
    errorMessage: "模拟的可重试解析失败",
  },
  {
    id: "job-processing",
    datasetId: "dataset-processing",
    sourceName: "architecture.md",
    status: "RUNNING",
    progress: 45,
    retryable: false,
  },
];

const initialSettings: SettingsResponse = {
  chat: {
    kind: "chat",
    baseUrl: "https://api.example.test/v1",
    modelName: "example-chat",
    timeoutSeconds: 30,
    apiKeyConfigured: true,
    apiKeyHint: "****8Kp2",
    thinkingEnabled: false,
  },
  embedding: {
    kind: "embedding",
    baseUrl: "https://api.example.test/v1",
    modelName: "example-embedding",
    timeoutSeconds: 30,
    apiKeyConfigured: true,
    apiKeyHint: "****8Kp2",
    defaultTopK: 6,
    embeddingDimension: 1024,
  },
  rerank: {
    kind: "rerank",
    baseUrl: "https://api.example.test/v1",
    modelName: "example-rerank",
    timeoutSeconds: 30,
    apiKeyConfigured: false,
    apiKeyHint: null,
    topN: 3,
  },
  rerankEnabled: false,
};

export const mockSettings: SettingsResponse = structuredClone(initialSettings);

let datasets = structuredClone(initialDatasets);
let jobs = structuredClone(initialJobs);
let generatedId = 1;

export function resetMockData(): void {
  datasets = structuredClone(initialDatasets);
  jobs = structuredClone(initialJobs);
  generatedId = 1;
  Object.assign(mockSettings.chat, initialSettings.chat);
  Object.assign(mockSettings.embedding, initialSettings.embedding);
  Object.assign(mockSettings.rerank, initialSettings.rerank);
  mockSettings.rerankEnabled = initialSettings.rerankEnabled;
}

export function listMockDatasets(): DatasetSummary[] {
  return datasets.map((dataset) => ({
    id: dataset.id,
    name: dataset.name,
    status: dataset.status,
    documentCount: dataset.documentCount,
    updatedAt: dataset.updatedAt,
  }));
}

export function findMockDataset(datasetId: string): DatasetDetail | undefined {
  return datasets.find((dataset) => dataset.id === datasetId);
}

export function createMockDataset(name: string): DatasetSummary {
  const id = `dataset-created-${generatedId++}`;
  const dataset: DatasetDetail = {
    id,
    name,
    status: "READY",
    documentCount: 0,
    updatedAt: new Date().toISOString(),
    documents: [],
  };
  datasets.unshift(dataset);
  return {
    id: dataset.id,
    name: dataset.name,
    status: dataset.status,
    documentCount: dataset.documentCount,
    updatedAt: dataset.updatedAt,
  };
}

export function listMockJobs(datasetId: string): Job[] {
  jobs.filter((job) => job.datasetId === datasetId).forEach((job) => {
    if (job.status === "RUNNING") {
      job.progress = Math.min(100, job.progress + 35);
      if (job.progress === 100) job.status = "SUCCEEDED";
    }
  });
  return jobs.filter((job) => job.datasetId === datasetId).map((job) => ({ ...job }));
}

export function findMockJob(jobId: string): Job | undefined {
  return jobs.find((job) => job.id === jobId);
}

export function createMockUploadJob(datasetId: string, filename: string): Job {
  const job: Job = {
    id: `job-upload-${generatedId++}`,
    datasetId,
    sourceName: filename,
    status: "RUNNING",
    progress: 15,
    retryable: false,
  };
  jobs.unshift(job);
  return { ...job };
}
