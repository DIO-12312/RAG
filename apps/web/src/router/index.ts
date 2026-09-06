import { createMemoryHistory, createWebHashHistory, createRouter, type Router } from "vue-router";

import type { AuthGate } from "@/stores/auth";
import DatasetsView from "@/views/DatasetsView.vue";
import DatasetDetailView from "@/views/DatasetDetailView.vue";
import LoginView from "@/views/LoginView.vue";
import OverviewView from "@/views/OverviewView.vue";
import SettingsView from "@/views/SettingsView.vue";
import ChatView from "@/views/ChatView.vue";

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
    ],
  });

  router.beforeEach(async (to) => {
    await auth.restore();
    return to.meta.requiresAuth && !auth.isAuthenticated ? { name: "login" } : true;
  });

  return router;
}
