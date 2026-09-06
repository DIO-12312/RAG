<script setup lang="ts">
import type { DatasetSummary } from "@/api/contracts";
import { computed } from "vue";
import AppIcon from "@/components/AppIcon.vue";
import StatusBadge from "@/components/StatusBadge.vue";

const props = defineProps<{ dataset: DatasetSummary }>();
const updatedAt = computed(() => { const date = new Date(props.dataset.updatedAt); if (Number.isNaN(date.getTime())) return "暂无更新时间"; return new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "medium",
  timeStyle: "short",
  hour12: false,
}).format(date); });
</script>
<template>
  <article class="dataset-card">
    <div class="card-top">
      <span class="icon-tile"><AppIcon name="library" /></span><StatusBadge :status="dataset.status" />
    </div>
    <RouterLink :to="`/datasets/${dataset.id}`">
      <strong>{{ dataset.name }}</strong>
    </RouterLink><p>{{ dataset.documentCount }} 个文档</p><div class="card-bottom">
      <small>{{ updatedAt }}</small><AppIcon name="arrow" />
    </div>
  </article>
</template>
