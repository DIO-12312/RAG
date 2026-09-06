import { defineStore } from "pinia";
import { listDatasets } from "@/api/datasets";
import type { DatasetSummary } from "@/api/contracts";
export const useDatasetStore = defineStore("datasets", {
  state: () => ({ items: [] as DatasetSummary[], loading: false, error: "" }),
  getters: { readyDatasets: (state) => state.items.filter((item) => item.status === "READY") },
  actions: { async load(): Promise<void> { this.loading = true; this.error = ""; try { this.items = await listDatasets(); } catch { this.error = "知识库加载失败，请稍后重试。"; } finally { this.loading = false; } } },
});
