<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { cancelJob, retryJob, reindexDocument, deleteDataset, deleteDocument, getDataset, listDatasetJobs, uploadDocument } from "@/api/datasets";
import type { DatasetDetail, Job } from "@/api/contracts";
import AppIcon from "@/components/AppIcon.vue";
import ConfirmDialog from "@/components/ConfirmDialog.vue";
import UploadPanel from "@/components/UploadPanel.vue"; import JobTable from "@/components/JobTable.vue";
import StatusBadge from "@/components/StatusBadge.vue";
import { useDatasetStore } from "@/stores/datasets";
import { useAuthStore } from "@/stores/auth";
import { randomUUID } from "@/utils/id";
const route=useRoute(); const router=useRouter(); const datasets=useDatasetStore(); const auth=useAuthStore(); const dataset=ref<DatasetDetail>(); const jobs=ref<Job[]>([]); const error=ref("");
const deleteDialogOpen=ref(false); const deleting=ref(false); let deleteRequestKey=randomUUID();
const selected=ref<string[]>([]); const busyBatch=ref(false); const batchNotice=ref("");
const documents=computed(()=>dataset.value?.documents??[]);
const allSelected=computed(()=>documents.value.length>0&&documents.value.every(d=>selected.value.includes(d.id)));
// 只有 RAG 标记为可重试的失败任务才能重试；不可重试的失败必须删除后重新上传，
// 否则用户点到的按钮必然返回 409「此任务不可重试」。
const retryableJobs=computed(()=>new Set(jobs.value.filter(j=>j.retryable).map(j=>j.id)));
const retryable=computed(()=>documents.value.filter(d=>selected.value.includes(d.id)&&d.status==="FAILED"&&d.jobId&&!d.stale&&retryableJobs.value.has(d.jobId)));
const reindexable=computed(()=>documents.value.filter(d=>selected.value.includes(d.id)&&d.status==="INDEXED"&&!d.stale));
const deleteMessage=computed(()=>`知识库“${dataset.value?.name ?? ""}”及其中的 ${dataset.value?.documentCount ?? 0} 个文档将被永久删除，且无法恢复。`);
let timer: ReturnType<typeof setTimeout> | undefined; let disposed=false;
function toggle(id:string,checked:boolean):void{selected.value=checked?[...new Set([...selected.value,id])]:selected.value.filter(item=>item!==id);}
function toggleAll(checked:boolean):void{selected.value=checked?documents.value.map(d=>d.id):[];}
async function batch(kind:"delete"|"retry"|"reindex"):Promise<void>{
  if(busyBatch.value||!selected.value.length)return;
  const targets=kind==="delete"?documents.value.filter(d=>selected.value.includes(d.id)):kind==="retry"?retryable.value:reindexable.value;
  if(!targets.length)return;
  if(kind==="delete"&&!window.confirm(`确认删除选中的 ${targets.length} 个文档？删除后将无法用于检索。`))return;
  busyBatch.value=true;error.value="";batchNotice.value="";
  const failed:string[]=[];
  for(const doc of targets){try{if(kind==="delete")await deleteDocument(doc.id);else if(kind==="retry")await retryJob(doc.jobId!);else await reindexDocument(doc.id);}catch{failed.push(doc.id);}}
  selected.value=failed;busyBatch.value=false;
  await load();
  const done=targets.length-failed.length;
  batchNotice.value=kind==="delete"?(failed.length?`已删除 ${done} 个，${failed.length} 个失败（仍选中，可重试）。`:`已删除 ${done} 个文档。`):(failed.length?`已提交 ${done} 个，${failed.length} 个失败。`:`已提交 ${done} 个文档的索引任务。`);
}
async function load(): Promise<void> {
  if(timer)clearTimeout(timer);
  try { const id=String(route.params.id); const [d,j]=await Promise.all([getDataset(id),listDatasetJobs(id)]); if(disposed)return; dataset.value=d; jobs.value=j; selected.value=selected.value.filter(item=>d.documents.some(doc=>doc.id===item)); error.value=""; }
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
async function reindex(id:string):Promise<void>{
  try{await reindexDocument(id);await load();}catch(e){error.value=e instanceof Error?e.message:"重新索引失败";}
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
      :max-upload-bytes="auth.user?.maxUploadBytes"
      @changed="load"
    /><h2>文档</h2><div
      v-if="documents.length"
      class="document-toolbar"
    >
      <label><input
        type="checkbox"
        :checked="allSelected"
        :disabled="busyBatch"
        @change="toggleAll(($event.target as HTMLInputElement).checked)"
      >全选</label><span>已选 {{ selected.length }} / {{ documents.length }}</span><button
        class="button-quiet"
        :disabled="busyBatch||!selected.length"
        @click="batch('delete')"
      >
        批量删除
      </button><button
        class="button-quiet"
        :disabled="busyBatch||!retryable.length"
        @click="batch('retry')"
      >
        重新索引失败项（{{ retryable.length }}）
      </button><button
        class="button-quiet"
        :disabled="busyBatch||!reindexable.length"
        @click="batch('reindex')"
      >
        重新索引已选（{{ reindexable.length }}）
      </button>
    </div><p
      v-if="batchNotice"
      role="status"
    >
      {{ batchNotice }}
    </p><article
      v-for="doc in documents"
      :key="doc.id"
    >
      <label><input
        type="checkbox"
        :checked="selected.includes(doc.id)"
        :disabled="busyBatch"
        @change="toggle(doc.id,($event.target as HTMLInputElement).checked)"
      ><span class="sr-only">选择 {{ doc.name }}</span></label><strong>{{ doc.name }}</strong> <StatusBadge :status="doc.status" /><small
        v-if="doc.stale"
        class="stale-hint"
      >索引元数据已丢失，请删除后重新上传</small><button
        v-if="doc.status==='INDEXED'&&!doc.stale"
        class="button-quiet"
        :disabled="busyBatch"
        @click="reindex(doc.id)"
      >
        重新索引
      </button><button
        class="button-quiet"
        :disabled="busyBatch"
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
