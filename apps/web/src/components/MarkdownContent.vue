<script setup lang="ts">
import { computed } from "vue";
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
const props = defineProps<{ content: string }>();
const parser = new MarkdownIt({ html: false, linkify: true, breaks: true });
// Remote images are rendered as links so answers cannot trigger tracking requests.
parser.renderer.rules.image = (tokens, i) => {
  const token = tokens[i]; const source = token.attrGet("src") || "";
  return '<a rel="noreferrer noopener" href="'+parser.utils.escapeHtml(String(source))+'">'+parser.utils.escapeHtml(token.content || "图片链接")+'</a>';
};
const html = computed(() => DOMPurify.sanitize(parser.render(props.content), { USE_PROFILES: { html: true }, FORBID_TAGS: ["img", "form", "input", "button", "style", "iframe"], FORBID_ATTR: ["style"] }));
</script>
<template>
  <!-- The only HTML sink receives Markdown with raw HTML disabled and DOMPurify sanitization. -->
  <!-- eslint-disable vue/no-v-html -->
  <div
    class="markdown-content"
    v-html="html"
  />
</template>
