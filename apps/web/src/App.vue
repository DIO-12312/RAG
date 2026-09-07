<script setup lang="ts">
import { RouterView, useRoute } from "vue-router";
import AppShell from "@/components/AppShell.vue";

const route = useRoute();
</script>

<template>
  <RouterView v-if="route.name === 'login'" />
  <AppShell v-else>
    <RouterView v-slot="{ Component }">
      <Transition
        name="page"
        mode="out-in"
      >
        <KeepAlive include="ChatView">
          <component
            :is="Component"
            :key="route.path"
          />
        </KeepAlive>
      </Transition>
    </RouterView>
  </AppShell>
</template>
