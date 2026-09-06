<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from "vue";
import { useRoute } from "vue-router";
import { cancelJob, retryJob, deleteDocument, getDataset, listDatasetJobs, uploadDocument } from "@/api/datasets";
import type { DatasetDetail, Job } from "@/api/contracts";
import UploadPanel from "@/components/UploadPanel.vue"; import JobTable from "@/components/JobTable.vue";
import StatusBadge from "@/components/StatusBadge.vue";
const route=useRoute(); const dataset=ref<DatasetDetail>(); const jobs=ref<Job[]>([]); const error=ref("");
let timer: ReturnType<typeof setTimeout> | undefined; let disposed=false;
async function load(): Promise<void> {
  if(timer)clearTimeout(timer);
  try { const id=String(route.params.id); const [d,j]=await Promise.all([getDataset(id),listDatasetJobs(id)]); if(disposed)return; dataset.value=d; jobs.value=j; error.value=""; }
  catch(e) { if(!disposed)error.value=e instanceof Error?e.message:"加载失败"; }
  if(!disposed && (jobs.value.some(j=>["PENDING","RUNNING"].includes(j.status)) || error.value))timer=setTimeout(()=>void load(),2500);
}
async function upload(file:File,key:string):Promise<void>{
  await uploadDocument(String(route.params.id),file,key);
}
async function action(id:string,kind:"cancel"|"retry"|"delete"):Promise<void>{
  if(kind==="delete"&&!window.confirm("确认删除此文档？删除后将无法用于检索。"))return;
  try{if(kind==="cancel")await cancelJob(id);else if(kind==="retry")await retryJob(id);else await deleteDocument(id);await load();}catch(e){error.value=e instanceof Error?e.message:"操作失败";}
}
onMounted(()=>void load());onBeforeUnmount(()=>{disposed=true;if(timer)clearTimeout(timer);});
</script>
<template>
  <main class="dataset-detail-page">
    <RouterLink to="/datasets">
      ← 知识库
    </RouterLink><h1>{{ dataset?.name }}</h1><p
      v-if="error"
      role="alert"
    >
      {{ error }} <button @click="load">
        重试
      </button>
    </p><UploadPanel
      :upload-file="upload"
      @changed="load"
    /><h2>文档</h2><article
      v-for="doc in dataset?.documents"
      :key="doc.id"
    >
      <strong>{{ doc.name }}</strong> <StatusBadge :status="doc.status" /><button
        class="button-quiet"
        @click="action(doc.id,'delete')"
      >
        删除
      </button>
    </article><JobTable
      :jobs="jobs"
      @retry="id=>action(id,'retry')"
      @cancel="id=>action(id,'cancel')"
    />
  </main>
</template>
