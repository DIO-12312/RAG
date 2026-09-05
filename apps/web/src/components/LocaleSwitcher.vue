<script setup lang="ts">
import { useLocaleStore } from "@/stores/locale";

const locale = useLocaleStore();

function toggleLocale(): void {
  locale.set(locale.value === "zh-CN" ? "en-US" : "zh-CN");
}
</script>

<template>
  <button
    aria-label="Language"
    type="button"
    @click="toggleLocale"
  >
    {{ locale.value === "zh-CN" ? "EN" : "中文" }}
  </button>
</template>
