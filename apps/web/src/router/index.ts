import { createMemoryHistory, createRouter, type Router } from "vue-router";

import type { AuthGate } from "@/stores/auth";
import DatasetsView from "@/views/DatasetsView.vue";
import LoginView from "@/views/LoginView.vue";
import OverviewView from "@/views/OverviewView.vue";

export function createAppRouter(auth: AuthGate = { isAuthenticated: false, restore: async () => {} }): Router {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/login", name: "login", component: LoginView },
      { path: "/", name: "overview", component: OverviewView, meta: { requiresAuth: true } },
      { path: "/datasets", name: "datasets", component: DatasetsView, meta: { requiresAuth: true } },
    ],
  });

  router.beforeEach(async (to) => {
    await auth.restore();
    return to.meta.requiresAuth && !auth.isAuthenticated ? { name: "login" } : true;
  });

  return router;
}
