<script setup lang="ts">
import { onMounted, ref } from "vue";
import DatasetCard from "@/components/DatasetCard.vue";
import { useDatasetStore } from "@/stores/datasets";
import { createDataset } from "@/api/datasets";
import AppIcon from "@/components/AppIcon.vue";
const datasets = useDatasetStore(); const name = ref(""); const error = ref(""); const busy = ref(false);
let requestKey = crypto.randomUUID();
onMounted(() => void datasets.load());
async function create(): Promise<void> {
  if (!name.value.trim() || busy.value) return; busy.value = true; error.value = "";
  try { await createDataset({name:name.value.trim()},requestKey); name.value = ""; requestKey = crypto.randomUUID(); await datasets.load(); }
  catch(e) { error.value = e instanceof Error ? e.message : "创建失败"; } finally { busy.value = false; }
}
</script>
<template>
  <main class="datasets-page">
    <header class="page-heading">
      <span class="eyebrow">KNOWLEDGE LIBRARY</span><h1>我的知识库<span class="heading-dot">.</span></h1><p>把零散的资料，变成触手可及的知识。</p>
    </header><form
      class="create-dataset-form"
      @submit.prevent="create"
    >
      <label>知识库名称<input
        v-model="name"
        maxlength="100"
        placeholder="为新的知识库起个名字…"
        required
      ></label><button :disabled="busy">
        <AppIcon name="plus" />{{ busy ? "创建中…" : "创建知识库" }}
      </button>
    </form><p
      v-if="error"
      role="alert"
    >
      {{ error }}
    </p><p v-if="datasets.loading">
      加载中…
    </p><p
      v-else-if="datasets.error"
      role="alert"
    >
      {{ datasets.error }} <button @click="datasets.load()">
        重试
      </button>
    </p><div
      v-else-if="!datasets.items.length"
      class="empty-library"
    >
      <span class="icon-tile"><AppIcon name="library" /></span><h2>为你的第一个灵感，留一个位置</h2><p>创建知识库后，即可上传文档并开始提问。</p>
    </div><div class="dataset-grid">
      <DatasetCard
        v-for="dataset in datasets.items"
        :key="dataset.id"
        :dataset="dataset"
      />
    </div>
  </main>
</template>
