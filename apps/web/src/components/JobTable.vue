<script setup lang="ts">
import type { Job } from "@/api/contracts";
import StatusBadge from "@/components/StatusBadge.vue";
defineProps<{ jobs: Job[] }>();
defineEmits<{ retry: [id: string]; cancel: [id: string] }>();
</script>
<template>
  <section>
    <h2>任务</h2><p v-if="!jobs.length">
      暂无任务
    </p><article
      v-for="job in jobs"
      :key="job.id"
      :data-job="job.id"
    >
      <strong>{{ job.sourceName }}</strong> <StatusBadge :status="job.status" /><progress
        :value="job.progress"
        max="100"
        :aria-label="job.sourceName+'处理进度'"
      /> {{ job.progress }}% <button
        v-if="job.status === 'FAILED' && job.retryable"
        @click="$emit('retry', job.id)"
      >
        重试
      </button><button
        v-if="job.status === 'PENDING' || job.status === 'RUNNING'"
        @click="$emit('cancel', job.id)"
      >
        取消
      </button>
    </article>
  </section>
</template>
