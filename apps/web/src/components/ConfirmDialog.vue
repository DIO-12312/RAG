<script setup lang="ts">
import { nextTick, ref, watch } from "vue";

const props = withDefaults(defineProps<{
  open: boolean;
  title?: string;
  message: string;
  confirmLabel?: string;
  busy?: boolean;
}>(), {
  title: "确认操作",
  confirmLabel: "确认",
  busy: false,
});
const emit = defineEmits<{ confirm: []; cancel: [] }>();
const cancelButton = ref<HTMLButtonElement>();

watch(() => props.open, async (open) => {
  if (!open) return;
  await nextTick();
  cancelButton.value?.focus();
});

function cancel(): void {
  if (!props.busy) emit("cancel");
}
</script>
<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="confirm-dialog-backdrop"
      @click.self="cancel"
      @keydown.esc="cancel"
    >
      <section
        class="confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-message"
      >
        <span
          class="confirm-dialog-mark"
          aria-hidden="true"
        >!</span>
        <h2 id="confirm-dialog-title">
          {{ title }}
        </h2>
        <p id="confirm-dialog-message">
          {{ message }}
        </p>
        <div class="confirm-dialog-actions">
          <button
            ref="cancelButton"
            class="button-quiet"
            :disabled="busy"
            @click="cancel"
          >
            取消
          </button>
          <button
            class="button-danger"
            :disabled="busy"
            @click="$emit('confirm')"
          >
            {{ busy ? "删除中…" : confirmLabel }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>
