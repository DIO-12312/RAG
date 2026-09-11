<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { cancelJob, retryJob, deleteDataset, deleteDocument, getDataset, listDatasetJobs, uploadDocument } from "@/api/datasets";
import type { DatasetDetail, Job } from "@/api/contracts";
import AppIcon from "@/components/AppIcon.vue";
import ConfirmDialog from "@/components/ConfirmDialog.vue";
import UploadPanel from "@/components/UploadPanel.vue"; import JobTable from "@/components/JobTable.vue";
import StatusBadge from "@/components/StatusBadge.vue";
import { useDatasetStore } from "@/stores/datasets";
import { randomUUID } from "@/utils/id";
const route=useRoute(); const router=useRouter(); const datasets=useDatasetStore(); const dataset=ref<DatasetDetail>(); const jobs=ref<Job[]>([]); const error=ref("");
const deleteDialogOpen=ref(false); const deleting=ref(false); let deleteRequestKey=randomUUID();
const deleteMessage=computed(()=>`知识库“${dataset.value?.name ?? ""}”及其中的 ${dataset.value?.documentCount ?? 0} 个文档将被永久删除，且无法恢复。`);
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
async function removeDataset():Promise<void>{
  if(deleting.value)return;
  if(timer){clearTimeout(timer);timer=undefined;}
  deleting.value=true;error.value="";
  try{
    await deleteDataset(String(route.params.id),deleteRequestKey);
    deleteRequestKey=randomUUID();deleteDialogOpen.value=false;
    await datasets.load();await router.replace("/datasets");
  }catch(e){error.value=e instanceof Error?e.message:"知识库删除失败";}
  finally{deleting.value=false;}
}
onMounted(()=>void load());onBeforeUnmount(()=>{disposed=true;if(timer)clearTimeout(timer);});
</script>
<template>
  <main class="dataset-detail-page">
    <RouterLink to="/datasets">
      ← 知识库
    </RouterLink><header class="dataset-detail-heading">
      <div><span class="eyebrow">KNOWLEDGE LIBRARY</span><h1>{{ dataset?.name }}</h1></div><button
        v-if="dataset"
        class="button-danger-quiet"
        :disabled="deleting"
        @click="deleteDialogOpen=true"
      >
        <AppIcon name="trash" />删除知识库
      </button>
    </header><p
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
    /><ConfirmDialog
      :open="deleteDialogOpen"
      title="删除知识库？"
      :message="deleteMessage"
      confirm-label="永久删除"
      :busy="deleting"
      @confirm="removeDataset"
      @cancel="deleteDialogOpen=false"
    />
  </main>
</template>
