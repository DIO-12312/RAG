<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue';
import type { Citation } from '@/api/contracts';
import AppIcon from './AppIcon.vue';
import MarkdownContent from './MarkdownContent.vue';

const props = defineProps<{ citation: Citation; anchor: HTMLElement; expanded: boolean }>();
const emit = defineEmits<{ expand: []; close: [restoreFocus: boolean]; keepOpen: []; leave: [] }>();
const titleId = useId();
const card = ref<HTMLElement>();
const closeButton = ref<HTMLButtonElement>();
const position = ref({ left: '12px', top: '12px', maxHeight: '360px' });
const copyStatus = ref('');
function place(): void {
  if (props.expanded) return;
  const rect = props.anchor.getBoundingClientRect();
  const width = card.value?.offsetWidth ?? 380;
  const below = window.innerHeight - rect.bottom - 20;
  const above = rect.top - 20;
  const useBelow = below >= Math.min(340, above);
  const height = Math.max(80, Math.min(360, useBelow ? below : above));
  const actualHeight = Math.min(card.value?.offsetHeight ?? height, height);
  position.value = {
    left: `${Math.max(12, Math.min(rect.left - 12, window.innerWidth - width - 12))}px`,
    top: `${Math.max(12, useBelow ? rect.bottom + 8 : rect.top - actualHeight - 8)}px`,
    maxHeight: `${height}px`,
  };
}
function onScroll(event: Event): void {
  if (!props.expanded && !(event.target instanceof Node && card.value?.contains(event.target))) emit('close', false);
}
function onOutside(event: PointerEvent): void {
  if (!props.expanded && event.target instanceof Node && !card.value?.contains(event.target) && !props.anchor.contains(event.target)) emit('close', false);
}
function onKey(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault();
    emit('close', props.expanded);
  }
  if (event.key !== 'Tab' || !props.expanded) return;
  const buttons = card.value?.querySelectorAll<HTMLButtonElement>('button');
  const first = buttons?.[0];
  const last = buttons?.[buttons.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
function copyWithSelection(text: string): boolean {
  const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const input = document.createElement('textarea');
  input.value = text;
  input.readOnly = true;
  input.style.position = 'fixed';
  input.style.opacity = '0';
  input.style.pointerEvents = 'none';
  document.body.appendChild(input);
  input.focus({ preventScroll: true });
  input.select();
  input.setSelectionRange(0, text.length);
  try {
    return typeof document.execCommand === 'function' && document.execCommand('copy');
  } catch {
    return false;
  } finally {
    input.remove();
    focused?.focus({ preventScroll: true });
  }
}
async function copy(): Promise<void> {
  const text = props.citation.evidence.content;
  let copied = false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      copied = true;
    }
  } catch {
    // 普通 HTTP 或权限受限时，回退到选区复制。
  }
  if (!copied) copied = copyWithSelection(text);
  copyStatus.value = copied ? '已复制' : '复制失败，请选择原文复制';
}
watch(() => [props.expanded, props.citation, props.anchor], async () => {
  copyStatus.value = '';
  await nextTick();
  if (props.expanded) closeButton.value?.focus();
  else place();
});
onMounted(async () => {
  document.addEventListener('keydown', onKey);
  document.addEventListener('pointerdown', onOutside);
  window.addEventListener('scroll', onScroll, true);
  window.addEventListener('resize', place);
  await nextTick();
  if (props.expanded) closeButton.value?.focus();
  else place();
});
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey);
  document.removeEventListener('pointerdown', onOutside);
  window.removeEventListener('scroll', onScroll, true);
  window.removeEventListener('resize', place);
});
</script>
<template>
  <Teleport to="body">
    <div
      :class="expanded ? 'citation-backdrop' : 'citation-floating'"
      @click.self="emit('close', expanded)"
    >
      <section
        ref="card"
        class="citation-card"
        :class="expanded ? 'citation-expanded' : 'citation-preview'"
        :style="expanded ? undefined : position"
        :role="expanded ? 'dialog' : 'region'"
        :aria-modal="expanded ? true : undefined"
        :aria-labelledby="titleId"
        @mouseenter="emit('keepOpen')"
        @mouseleave="emit('leave')"
        @focusin="emit('keepOpen')"
        @focusout="emit('leave')"
      >
        <header class="citation-header">
          <span class="citation-source-icon"><AppIcon name="file" /></span>
          <div class="citation-heading">
            <small>引用来源 · {{ citation.ordinal }}</small>
            <h3 :id="titleId">
              {{ citation.evidence.sourceName }}
            </h3>
            <p v-if="citation.evidence.locator">
              {{ citation.evidence.locator }}
            </p>
          </div>
          <button
            v-if="expanded"
            ref="closeButton"
            type="button"
            class="citation-quiet"
            aria-label="关闭来源"
            @click="emit('close', true)"
          >
            关闭
          </button>
        </header>
        <div class="citation-body">
          <MarkdownContent
            v-if="expanded"
            :content="citation.evidence.content"
          />
          <p
            v-else
            class="citation-original"
          >
            {{ citation.evidence.content }}
          </p>
        </div>
        <footer class="citation-footer">
          <span>{{ expanded ? '来源原文' : '来源片段' }}</span>
          <template v-if="expanded">
            <span
              v-if="copyStatus"
              role="status"
            >{{ copyStatus }}</span>
            <button
              type="button"
              class="citation-quiet"
              @click="copy"
            >
              复制原文
            </button>
          </template>
          <button
            v-else
            type="button"
            class="citation-quiet"
            @click="emit('expand')"
          >
            展开完整来源 ↗
          </button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>
<style scoped>
.citation-floating { position: fixed; inset: 0; z-index: 1000; pointer-events: none; }
.citation-card { display: flex; flex-direction: column; overflow: hidden; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 18px; box-shadow: 0 18px 64px #29203226; pointer-events: auto; text-align: left; }
.citation-preview { position: fixed; width: min(380px, calc(100vw - 24px)); }
.citation-header { display: flex; gap: 12px; align-items: flex-start; padding: 20px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.citation-source-icon { display: grid; place-items: center; width: 38px; height: 42px; border-radius: 10px; background: var(--accent-soft); color: var(--accent); flex-shrink: 0; }
.citation-heading { flex: 1; min-width: 0; }
.citation-heading small { font-size: 11px; color: var(--accent); }
.citation-heading h3 { margin: 5px 0; font-size: 15px; line-height: 1.5; overflow-wrap: anywhere; }
.citation-heading p { margin: 0; color: var(--text-muted); font-size: 12px; }
.citation-body { overflow: auto; overscroll-behavior: contain; padding: 18px 22px; min-height: 0; }
.citation-original { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 14px; line-height: 1.9; user-select: text; }
.citation-footer { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 12px 20px; border-top: 1px solid var(--border); background: var(--surface-muted); flex-shrink: 0; font-size: 12px; color: var(--text-muted); }
.citation-footer > :first-child { margin-right: auto; }
.citation-quiet { padding: 6px 10px; border: 0; border-radius: 8px; background: var(--accent-soft); color: var(--accent-strong); box-shadow: none; font-size: 12px; flex-shrink: 0; cursor: pointer; }
.citation-quiet:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.citation-backdrop { position: fixed; inset: 0; z-index: 1100; display: grid; place-items: center; padding: 24px; background: #21192c66; backdrop-filter: blur(4px); }
.citation-expanded { width: min(860px, 100%); max-height: min(82dvh, 920px); }
.citation-expanded .citation-header { padding: 24px 30px; }
.citation-expanded .citation-body { padding: 28px 34px; }
.citation-expanded .citation-body { font-size: 15px; }
@media (max-width: 600px) {
  .citation-backdrop { padding: 12px; }
  .citation-expanded { max-height: 90dvh; }
  .citation-expanded .citation-header, .citation-expanded .citation-body { padding: 20px; }
}
</style>
