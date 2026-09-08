<script setup lang="ts">
import { computed, onBeforeUnmount, onDeactivated, ref, shallowRef, watch } from "vue";
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import type { Citation } from "@/api/contracts";
import CitationSourceCard from "./CitationSourceCard.vue";

const props = defineProps<{ content: string; citations?: Citation[] }>();
const sources = computed(() => new Map((props.citations ?? []).map(c => [c.ordinal, c])));
const selected = shallowRef<Citation>();
const anchor = shallowRef<HTMLElement>();
const expanded = ref(false);
let restoringFocus = false;
let closeTimer: ReturnType<typeof setTimeout> | undefined;
function keepOpen(): void { clearTimeout(closeTimer); }
function close(restoreFocus = false): void {
  keepOpen();
  selected.value = undefined;
  expanded.value = false;
  if (restoreFocus) {
    restoringFocus = true;
    anchor.value?.focus({ preventScroll: true });
    restoringFocus = false;
  }
}
function scheduleClose(): void {
  keepOpen();
  if (!expanded.value) closeTimer = setTimeout(() => close(), 180);
}
function show(event: Event, expand = false): void {
  const target = event.target instanceof Element ? event.target.closest<HTMLElement>('.citation-marker') : null;
  if (!target || restoringFocus || (expanded.value && !expand)) return;
  const citation = sources.value.get(Number(target.dataset.citation));
  if (!citation) return;
  keepOpen();
  anchor.value = target;
  selected.value = citation;
  expanded.value = expand;
}
function openWithKeyboard(event: KeyboardEvent): void {
  if ((event.key === 'Enter' || event.key === ' ') && event.target instanceof Element && event.target.closest('.citation-marker')) {
    event.preventDefault();
    show(event, true);
  }
}
watch(() => [props.content, props.citations], () => close());
onBeforeUnmount(() => close());
onDeactivated(() => close());

const parser = new MarkdownIt({ html: false, linkify: true, breaks: true });
// Only prose text is eligible: never rewrite code, link labels or destinations.
parser.core.ruler.push('citation_text', state => {
  for (const block of state.tokens) {
    if (block.type !== 'inline') continue;
    let linkDepth = 0;
    for (const token of block.children ?? []) {
      if (token.type === 'link_open') linkDepth++;
      if (token.type === 'link_close') linkDepth--;
      if (token.type === 'text' && linkDepth === 0) token.type = 'citation_text';
    }
  }
});
parser.renderer.rules.citation_text = (tokens, i) => {
  const text = parser.utils.escapeHtml(tokens[i]!.content);
  return text.replace(/\[(\d+)\]/g, (match, number: string) => {
    const citation = sources.value.get(Number(number));
    if (!citation) return match;
    const ordinal = citation.ordinal;
    const label = parser.utils.escapeHtml(`查看来源 ${ordinal}：${citation.evidence.sourceName}`);
    return `<button type="button" class="citation-marker" data-citation="${ordinal}" aria-label="${label}" aria-haspopup="dialog">${ordinal}</button>`;
  });
};
// Remote images remain links so answers cannot trigger tracking requests.
parser.renderer.rules.image = (tokens, i) => {
  const token = tokens[i]!;
  const source = token.attrGet("src") || "";
  return '<a rel="noreferrer noopener" href="'+parser.utils.escapeHtml(String(source))+'">'+parser.utils.escapeHtml(token.content || "图片链接")+'</a>';
};
// Raw HTML is disabled; only our renderer can create citation buttons.
const html = computed(() => DOMPurify.sanitize(parser.render(props.content), {
  USE_PROFILES: { html: true },
  FORBID_TAGS: ["img", "form", "input", "style", "iframe"], FORBID_ATTR: ["style"],
}));
</script>
<template>
  <div
    @click="show($event, true)"
    @mouseover="show($event)"
    @mouseout="scheduleClose"
    @focusin="show($event)"
    @focusout="scheduleClose"
    @keydown="openWithKeyboard"
  >
    <!-- The only HTML sink receives sanitized Markdown with raw HTML disabled. -->
    <!-- eslint-disable vue/no-v-html -->
    <div
      class="markdown-content"
      v-html="html"
    />
    <!-- eslint-enable vue/no-v-html -->
  </div>
  <CitationSourceCard
    v-if="selected && anchor"
    :citation="selected"
    :anchor="anchor"
    :expanded="expanded"
    @expand="expanded = true"
    @close="close($event)"
    @keep-open="keepOpen"
    @leave="scheduleClose"
  />
</template>
<style scoped>
:deep(.citation-marker) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  vertical-align: .12em;
  width: 23px;
  height: 23px;
  min-height: 23px;
  padding: 0;
  margin: 0 3px;
  border: 1px solid #d9cbed;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent-strong);
  font: 600 11px/1 system-ui, sans-serif;
  cursor: pointer;
  box-shadow: none;
  transition: background .15s, color .15s;
}
:deep(.citation-marker:hover), :deep(.citation-marker:focus-visible) {
  background: var(--accent-strong);
  color: white;
  outline: 2px solid #c4b2e1;
  outline-offset: 2px;
  transform: none;
}
</style>
