import { createApp } from "vue";
import { createPinia } from "pinia";

import App from "./App.vue";
import { createAppRouter } from "./router";
import { useAuthStore } from "./stores/auth";
import "./styles/global.css";

async function bootstrap(): Promise<void> {
  if (import.meta.env.VITE_USE_MOCK === "true") {
    const { mockWorker } = await import("./mocks/browser");
    await mockWorker.start({ onUnhandledRequest: "bypass" });
  } else if ("serviceWorker" in navigator) {
    const registrations = await navigator.serviceWorker.getRegistrations();
    for (const registration of registrations) {
      if (registration.active?.scriptURL.endsWith("/mockServiceWorker.js")) await registration.unregister();
    }
  }

  const app = createApp(App);
  const pinia = createPinia();
  const auth = useAuthStore(pinia);

  app.use(pinia);
  const router = createAppRouter(auth);
  app.use(router);
  window.addEventListener("auth-expired", () => {
    const wasLoggedIn = auth.isAuthenticated;
    auth.user = null; auth.expirationNotice = "登录已过期，请重新登录。";
    if (wasLoggedIn) void router.push("/login");
  });
  app.mount("#app");
}

void bootstrap();
