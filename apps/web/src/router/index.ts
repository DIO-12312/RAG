import { createMemoryHistory, createWebHashHistory, createRouter, type Router } from "vue-router";

import type { AuthGate } from "@/stores/auth";
import DatasetsView from "@/views/DatasetsView.vue";
import DatasetDetailView from "@/views/DatasetDetailView.vue";
import LoginView from "@/views/LoginView.vue";
import OverviewView from "@/views/OverviewView.vue";
import SettingsView from "@/views/SettingsView.vue";
import ChatView from "@/views/ChatView.vue";
import ObservabilityView from "@/views/ObservabilityView.vue";

export function createAppRouter(auth: AuthGate = { isAuthenticated: false, restore: async () => {} }): Router {
  const router = createRouter({
    history: import.meta.env.MODE === "test" ? createMemoryHistory() : createWebHashHistory(),
    routes: [
      { path: "/login", name: "login", component: LoginView },
      { path: "/", name: "overview", component: OverviewView, meta: { requiresAuth: true } },
      { path: "/datasets", name: "datasets", component: DatasetsView, meta: { requiresAuth: true } },
      { path: "/datasets/:id", name: "dataset-detail", component: DatasetDetailView, meta: { requiresAuth: true } },
      { path: "/chat", name: "chat", component: ChatView, meta: { requiresAuth: true } },
      { path: "/settings", name: "settings", component: SettingsView, meta: { requiresAuth: true } },
      { path: "/admin/observability", name: "observability", component: ObservabilityView, meta: { requiresAuth: true, requiresAdmin: true } },
    ],
  });

  router.beforeEach(async (to) => {
    await auth.restore();
    if (to.meta.requiresAuth && !auth.isAuthenticated) return { name: "login" };
    if (to.meta.requiresAdmin) {
      try { await auth.refresh?.(); }
      catch { return auth.isAuthenticated ? { name: "overview" } : { name: "login" }; }
      if (!auth.isAuthenticated) return { name: "login" };
      if (!auth.isAdmin) return { name: "overview" };
    }
    return true;
  });

  return router;
}
