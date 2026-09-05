<script setup lang="ts">
import LocaleSwitcher from "@/components/LocaleSwitcher.vue";
import { messages } from "@/i18n/messages";
import { useLocaleStore } from "@/stores/locale";

const locale = useLocaleStore();
</script>

<template>
  <div class="app-shell">
    <header class="app-shell__header">
      <strong>RAG MVP</strong>
      <LocaleSwitcher />
    </header>
    <nav aria-label="Primary navigation">
      <span>{{ messages[locale.value].overview }}</span>
      <span>{{ messages[locale.value].datasets }}</span>
      <span>{{ messages[locale.value].chat }}</span>
      <span>{{ messages[locale.value].settings }}</span>
    </nav>
    <main><slot /></main>
  </div>
</template>
