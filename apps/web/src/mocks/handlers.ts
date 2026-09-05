import { http, HttpResponse } from "msw";

import type {
  CreateDatasetRequest,
  CurrentUser,
  UpdateAgentSettingsRequest,
  UpdateChatModelConfigRequest,
  UpdateEmbeddingModelConfigRequest,
  UpdateRerankModelConfigRequest,
  UploadDocumentRequest,
} from "@/api/contracts";
import {
  createMockDataset,
  createMockUploadJob,
  findMockDataset,
  findMockJob,
  listMockDatasets,
  listMockJobs,
  mockSettings,
  resetMockData,
} from "./data";

let signedIn = false;

const currentUser: CurrentUser = {
  id: "user-demo",
  email: "demo@example.test",
  language: "zh-CN",
};

export function resetMockState(): void {
  signedIn = false;
  resetMockData();
}

function requireSignedIn(): HttpResponse<{ code: string; message: string }> | undefined {
  if (!signedIn) {
    return HttpResponse.json(
      { code: "AUTH_EXPIRED", message: "登录已过期，请重新登录。" },
      { status: 401 },
    );
  }
}

export const handlers = [
  http.post("*/auth/login", () => {
    signedIn = true;
    return HttpResponse.json(currentUser);
  }),

  http.post("*/auth/logout", () => {
    signedIn = false;
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("*/me", () => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;

    return HttpResponse.json(currentUser);
  }),

  http.get("*/datasets", () => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    return HttpResponse.json(listMockDatasets());
  }),

  http.post("*/datasets", async ({ request }) => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    const payload = (await request.json()) as CreateDatasetRequest;
    return HttpResponse.json(createMockDataset(payload.name), { status: 201 });
  }),

  http.get("*/datasets/:id", ({ params }) => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    const dataset = findMockDataset(String(params.id));
    return dataset
      ? HttpResponse.json(dataset)
      : HttpResponse.json({ code: "DATASET_NOT_FOUND", message: "知识库不存在。" }, { status: 404 });
  }),

  http.post("*/datasets/:id/documents", async ({ params, request }) => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    const datasetId = String(params.id);
    if (!findMockDataset(datasetId)) {
      return HttpResponse.json({ code: "DATASET_NOT_FOUND", message: "知识库不存在。" }, { status: 404 });
    }
    const payload = (await request.json()) as UploadDocumentRequest;
    return HttpResponse.json(createMockUploadJob(datasetId, payload.filename), { status: 202 });
  }),

  http.get("*/datasets/:id/jobs", ({ params }) => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    return HttpResponse.json(listMockJobs(String(params.id)));
  }),

  http.post("*/jobs/:id/cancel", ({ params }) => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    const job = findMockJob(String(params.id));
    if (!job) return HttpResponse.json({ code: "JOB_NOT_FOUND", message: "任务不存在。" }, { status: 404 });
    job.status = "CANCELLED";
    return HttpResponse.json(job);
  }),

  http.post("*/jobs/:id/retry", ({ params }) => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    const job = findMockJob(String(params.id));
    if (!job) return HttpResponse.json({ code: "JOB_NOT_FOUND", message: "任务不存在。" }, { status: 404 });
    job.status = "RUNNING";
    job.progress = 0;
    job.retryable = false;
    return HttpResponse.json(job);
  }),

  http.delete("*/documents/:id", () => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("*/settings", () => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    return HttpResponse.json(mockSettings);
  }),

  http.put("*/settings/models/chat", async ({ request }) => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    const payload = (await request.json()) as UpdateChatModelConfigRequest;
    const { apiKey, ...config } = payload;
    Object.assign(mockSettings.chat, config, {
      apiKeyConfigured: Boolean(apiKey) || mockSettings.chat.apiKeyConfigured,
    });
    return HttpResponse.json(mockSettings);
  }),

  http.put("*/settings/models/embedding", async ({ request }) => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    const payload = (await request.json()) as UpdateEmbeddingModelConfigRequest;
    const { apiKey, ...config } = payload;
    Object.assign(mockSettings.embedding, config, {
      apiKeyConfigured: Boolean(apiKey) || mockSettings.embedding.apiKeyConfigured,
    });
    return HttpResponse.json(mockSettings);
  }),

  http.put("*/settings/models/rerank", async ({ request }) => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    const payload = (await request.json()) as UpdateRerankModelConfigRequest;
    const { apiKey, ...config } = payload;
    Object.assign(mockSettings.rerank, config, {
      apiKeyConfigured: Boolean(apiKey) || mockSettings.rerank.apiKeyConfigured,
    });
    return HttpResponse.json(mockSettings);
  }),

  http.put("*/settings/agent", async ({ request }) => {
    const unauthorized = requireSignedIn();
    if (unauthorized) return unauthorized;
    const payload = (await request.json()) as UpdateAgentSettingsRequest;
    mockSettings.rerankEnabled = payload.rerankEnabled;
    return HttpResponse.json(mockSettings);
  }),
];
