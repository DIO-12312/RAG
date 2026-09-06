<script setup lang="ts">
defineProps<{ status: string }>();
const labels: Record<string,string> = {READY:"可提问",EMPTY:"待上传",PROCESSING:"处理中",FAILED:"处理失败",INDEXED:"已索引",PENDING:"等待中",RUNNING:"运行中",SUCCEEDED:"已完成",CANCELLED:"已取消"};
</script>
<template>
  <span
    class="status-badge"
    :data-status="status"
  ><i aria-hidden="true" />{{ labels[status] || status }}</span>
</template>
