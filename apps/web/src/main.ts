import { createApp } from "vue";
import { createPinia } from "pinia";

import App from "./App.vue";
import { createAppRouter } from "./router";
import { useAuthStore } from "./stores/auth";
import "./styles/global.css";

async function bootstrap(): Promise<void> {
  if (import.meta.env.DEV) {
    const { mockWorker } = await import("./mocks/browser");
    await mockWorker.start({ onUnhandledRequest: "bypass" });
  }

  const app = createApp(App);
  const pinia = createPinia();
  const auth = useAuthStore(pinia);

  app.use(pinia);
  app.use(createAppRouter(auth));
  app.mount("#app");
}

void bootstrap();
