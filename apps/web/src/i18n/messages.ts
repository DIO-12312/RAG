import type { Locale } from "@/stores/locale";

export const messages: Record<Locale, Record<"overview" | "datasets" | "chat" | "settings" | "observability", string>> = {
  "zh-CN": {
    overview: "概览",
    datasets: "知识库",
    chat: "对话",
    settings: "设置",
    observability: "观测",
  },
  "en-US": {
    overview: "Overview",
    datasets: "Datasets",
    chat: "Chat",
    settings: "Settings",
    observability: "Observability",
  },
};
