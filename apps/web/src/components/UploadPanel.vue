<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import AppIcon from "@/components/AppIcon.vue";
import { randomUUID } from "@/utils/id";
const props = defineProps<{ uploadFile: (file: File, key: string) => Promise<void> }>();
const emit = defineEmits<{ changed: [] }>();
type Entry = { id: string; file: File; status: "等待上传" | "上传中" | "已提交" | "上传失败" | "已跳过"; error: string };
const entries = ref<Entry[]>([]);
const running = ref(false);
const notice = ref("");
let disposed = false;
onBeforeUnmount(() => { disposed = true; });
const pending = computed(() => entries.value.filter(e => e.status === "等待上传" || e.status === "上传失败"));
const completed = computed(() => entries.value.filter(e => e.status === "已提交").length);
function selected(event: Event): void {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  const known = new Set(entries.value.map(e => [e.file.webkitRelativePath || e.file.name, e.file.size, e.file.lastModified].join("|")));
  for (const file of files) {
    const fingerprint = [file.webkitRelativePath || file.name, file.size, file.lastModified].join("|");
    if (known.has(fingerprint)) continue;
    known.add(fingerprint);
    if (entries.value.length >= 1000) { notice.value = "单批最多选择 1000 个文件，请分批上传。"; break; }
    const error = !/\.(pdf|md|txt|py|go|js|ts|java|chm|chi)$/i.test(file.name) ? "不支持的文件格式" : file.size > 32*1024*1024 ? "文件超过 32 MB" : file.size === 0 ? "空文件" : "";
    entries.value.push({ id: randomUUID(), file, status: error ? "已跳过" : "等待上传", error });
  }
  input.value = "";
}
async function submit(): Promise<void> {
  if (running.value || !pending.value.length) return;
  running.value = true;
  const batch = [...pending.value];
  try {
    for (const entry of batch) {
      if (disposed) break;
      entry.status = "上传中"; entry.error = "";
      try { await props.uploadFile(entry.file, entry.id); entry.status = "已提交"; emit("changed"); }
      catch (e) { entry.status = "上传失败"; entry.error = e instanceof Error ? e.message : "上传失败"; }
    }
  } finally { running.value = false; }
}
function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}
function fileIcon(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (ext === "pdf") return "file";
  if (ext === "md") return "file";
  if (["py", "go", "js", "ts", "java"].includes(ext)) return "file";
  return "file";
}
</script>
<template>
  <section class="upload-panel">
    <span class="icon-tile"><AppIcon name="upload" /></span><h2>把新的知识带进来</h2><p>多选文件或整个文件夹 · 单文件最大 32 MB</p>
    <div class="upload-pickers">
      <label>选择文件<input
        type="file"
        multiple
        accept=".pdf,.md,.txt,.py,.go,.js,.ts,.java,.chm,.chi"
        :disabled="running"
        @change="selected"
      ></label><label>选择文件夹<input
        type="file"
        multiple
        webkitdirectory
        :disabled="running"
        @change="selected"
      ></label>
    </div>
    <p
      v-if="notice"
      role="alert"
    >
      {{ notice }}
    </p>
    <template v-if="entries.length">
      <p role="status">
        已选择 {{ entries.length }} 个文件 · 已提交 {{ completed }} 个（索引进度见下方任务）
      </p>
      <div class="upload-file-grid">
        <div
          v-for="entry in entries"
          :key="entry.id"
          class="upload-file-card"
          :class="{ 'upload-file-card--error': entry.error, 'upload-file-card--done': entry.status === '已提交' }"
        >
          <span class="upload-file-icon"><AppIcon :name="fileIcon(entry.file.name)" /></span>
          <span class="upload-file-name">{{ entry.file.webkitRelativePath || entry.file.name }}</span>
          <span class="upload-file-size">{{ formatSize(entry.file.size) }}</span>
          <small class="upload-file-status" :class="{ 'upload-error': entry.error }">{{ entry.status }}{{ entry.error ? '：' + entry.error : '' }}</small>
        </div>
      </div>
      <div class="upload-actions">
        <button
          :disabled="running || !pending.length"
          @click="submit"
        >
          {{ running ? "正在逐个上传…" : "上传待处理文件 / 重试失败文件" }}
        </button><button
          class="button-quiet"
          :disabled="running"
          @click="entries=[];notice=''"
        >
          清空列表
        </button>
      </div>
    </template>
    <p v-else>
      支持 PDF、CHM/CHI、Markdown、文本与代码。不支持的文件将跳过。
    </p>
  </section>
</template>
