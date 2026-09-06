<script setup lang="ts">
defineProps<{ open: boolean; message: string }>();
defineEmits<{ confirm: []; cancel: [] }>();
</script>
<template>
  <div v-if="open">
    <p>{{ message }}</p><button @click="$emit('confirm')">
      确认
    </button><button @click="$emit('cancel')">
      取消
    </button>
  </div>
</template>
