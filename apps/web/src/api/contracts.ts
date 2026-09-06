export type ModelKind = "chat" | "embedding" | "rerank";

export const rootRoutes = [
  "/auth/register",
  "/auth/login",
  "/auth/logout",
  "/me",
  "/datasets",
  "/datasets/:id",
  "/datasets/:id/documents",
  "/datasets/:id/jobs",
  "/jobs/:id/cancel",
  "/jobs/:id/retry",
  "/documents/:id",
  "/settings",
  "/settings/models/chat",
  "/settings/models/embedding",
  "/settings/models/rerank",
  "/settings/agent",
  "/chat/stream",
] as const;

export interface CurrentUser {
  id: string;
  email: string;
  language: "zh-CN" | "en-US";
}

export interface LoginRequest {
  email: string;
  password: string;
}

export type JobStatus = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
export type DatasetStatus = "READY" | "PROCESSING" | "FAILED" | "EMPTY";

export interface DatasetSummary {
  id: string;
  name: string;
  status: DatasetStatus;
  documentCount: number;
  updatedAt: string;
}

export interface DatasetDetail extends DatasetSummary {
  documents: DocumentSummary[];
}

export interface DocumentSummary {
  id: string;
  name: string;
  status: "INDEXED" | "PROCESSING" | "FAILED";
}

export interface CreateDatasetRequest {
  name: string;
}

export interface UploadDocumentRequest {
  filename: string;
}

export interface Job {
  id: string;
  datasetId: string;
  sourceName: string;
  status: JobStatus;
  progress: number;
  retryable: boolean;
  errorMessage?: string;
}

export interface UpdateModelConfigRequest {
  baseUrl: string;
  modelName: string;
  timeoutSeconds: number;
  apiKey?: string;
}

export interface UpdateChatModelConfigRequest extends UpdateModelConfigRequest {
  thinkingEnabled: boolean;
}

export interface UpdateEmbeddingModelConfigRequest extends UpdateModelConfigRequest {
  defaultTopK: number;
  embeddingDimension: number;
}

export interface UpdateRerankModelConfigRequest extends UpdateModelConfigRequest {
  topN: number;
}

export interface UpdateAgentSettingsRequest {
  rerankEnabled: boolean;
}

export interface ScoreBreakdown {
  fusionScore: number;
  rerankScore?: number;
}

export interface Evidence {
  chunkId: string;
  content: string;
  sourceName: string;
  locator: string;
  scores: ScoreBreakdown;
}

export interface Citation {
  ordinal: number;
  evidence: Evidence;
}

export interface ChatRequest {
  conversationId?: string;
  datasetId: string;
  question: string;
}

export type ChatEvent =
  | { type: "retrieval"; hits: Evidence[] }
  | { type: "token"; text: string }
  | { type: "final"; answer: string; citations: Citation[]; conversationId?: string }
  | { type: "error"; code: string; message: string };

export interface ModelConfigResponse {
  kind: ModelKind;
  baseUrl: string;
  modelName: string;
  timeoutSeconds: number;
  apiKeyConfigured: boolean;
  apiKeyHint: string | null;
}

export interface ChatModelConfigResponse extends ModelConfigResponse {
  kind: "chat";
  thinkingEnabled: boolean;
}

export interface EmbeddingModelConfigResponse extends ModelConfigResponse {
  kind: "embedding";
  defaultTopK: number;
  embeddingDimension: number;
}

export interface RerankModelConfigResponse extends ModelConfigResponse {
  kind: "rerank";
  topN: number;
}

export interface SettingsResponse {
  chat: ChatModelConfigResponse;
  embedding: EmbeddingModelConfigResponse;
  rerank: RerankModelConfigResponse;
  rerankEnabled: boolean;
}
