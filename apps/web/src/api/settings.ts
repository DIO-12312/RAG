import type {
  SettingsResponse,
  UpdateAgentSettingsRequest,
  UpdateChatModelConfigRequest,
  UpdateEmbeddingModelConfigRequest,
  UpdateRerankModelConfigRequest,
} from "./contracts";
import { request } from "./http";

export function getSettings(): Promise<SettingsResponse> {
  return request<SettingsResponse>("/settings");
}

export function updateChatSettings(payload: UpdateChatModelConfigRequest): Promise<SettingsResponse> {
  return request<SettingsResponse>("/settings/models/chat", { method: "PUT", body: JSON.stringify(payload) });
}

export function updateEmbeddingSettings(payload: UpdateEmbeddingModelConfigRequest): Promise<SettingsResponse> {
  return request<SettingsResponse>("/settings/models/embedding", { method: "PUT", body: JSON.stringify(payload) });
}

export function updateRerankSettings(payload: UpdateRerankModelConfigRequest): Promise<SettingsResponse> {
  return request<SettingsResponse>("/settings/models/rerank", { method: "PUT", body: JSON.stringify(payload) });
}

export function updateAgentSettings(payload: UpdateAgentSettingsRequest): Promise<SettingsResponse> {
  return request<SettingsResponse>("/settings/agent", { method: "PUT", body: JSON.stringify(payload) });
}
