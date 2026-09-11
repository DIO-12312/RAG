<script setup lang="ts">
defineProps<{ name: string }>();
const paths: Record<string, string> = {
  home: 'M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z',
  library: 'M4 4h5v16H4zM10 4h5v16h-5M17 5l4 14-4 1-4-14',
  chat: 'M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3v-3H3V6a2 2 0 0 1 2-2ZM8 9h8M8 13h5',
  settings: 'M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M9 15v6',
  spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5ZM20 2v4M18 4h4',
  arrow: 'M5 12h14m-6-6 6 6-6 6', plus: 'M12 5v14M5 12h14',
  file: 'M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8M8 16h5',
  upload: 'M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6', send: 'M12 19V5m-6 6 6-6 6 6',
  shield: 'm12 3 8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6Zm-4 9 3 3 5-6',
  trash: 'M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7m4 4v6m4-6v6',
};
</script>
<template>
  <svg
    class="app-icon"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  ><path :d="paths[name] || paths.spark" /></svg>
</template>
