<script setup lang="ts">
import {onMounted,onActivated,onBeforeUnmount,computed,ref,watch,nextTick} from "vue";import {streamChat,type ChatStream} from "@/api/chat";import {useDatasetStore} from "@/stores/datasets";import type {Citation} from "@/api/contracts";import {request} from "@/api/http";
import AppIcon from "@/components/AppIcon.vue";
import KnowledgeOrb from "@/components/KnowledgeOrb.vue";
import MarkdownContent from "@/components/MarkdownContent.vue";
import HistoryDrawer from "@/components/HistoryDrawer.vue";
import {useRoute,useRouter} from "vue-router";
import {randomUUID} from "@/utils/id";
defineOptions({name:'ChatView'});
const route=useRoute();const router=useRouter();
const datasets=useDatasetStore();const selectedId=ref("");const question=ref("");const answer=ref("");const citations=ref<Citation[]>([]);const error=ref("");const phase=ref("");const busy=ref(false);const conversationId=ref("");const transcript=ref<{role:string;content:string;citations:Citation[]}[]>([]);const conversations=ref<{id:string;datasetId:string;title:string;updatedAt:string}[]>([]);
const QUESTION_MAX_BYTES=8000;
const questionBytes=computed(()=>new TextEncoder().encode(question.value).length);
const questionTooLong=computed(()=>questionBytes.value>QUESTION_MAX_BYTES);
const contextUsage=ref<{estimatedTokens:number;usableTokens:number;evidenceCount:number;evidenceLimit:number}|undefined>();
const contextWarning=computed(()=>{const usage=contextUsage.value;if(!usage||!usage.usableTokens)return "";const ratio=usage.estimatedTokens/usage.usableTokens;const capped=usage.evidenceLimit>0&&usage.evidenceCount>=usage.evidenceLimit;if(ratio<0.8&&!capped)return "";const percent=Math.round(ratio*100);const detail=`约 ${usage.estimatedTokens.toLocaleString()} / ${usage.usableTokens.toLocaleString()} tokens`;if(capped&&ratio<0.8)return `证据条数已达上限（${usage.evidenceCount} / ${usage.evidenceLimit}），更早或更低分的证据会被丢弃。`;if(ratio>=0.95)return `上下文已用 ${percent}%（${detail}），接近上限，较早的对话与部分证据可能被裁剪。`;return `上下文已用 ${percent}%（${detail}，证据 ${usage.evidenceCount} 条），继续追问可能触发裁剪。`;});
async function refreshHistory():Promise<void>{try{conversations.value=await request('/conversations');}catch(e){error.value=e instanceof Error?e.message:'历史会话加载失败';}}
async function deleteConversations(ids:string[]):Promise<void>{if(busy.value||!ids.length)return;busy.value=true;error.value='';try{for(const id of ids)await request("/conversations/"+encodeURIComponent(id),{method:"DELETE"});if(ids.includes(conversationId.value))newChat();await refreshHistory();}catch(e){error.value=e instanceof Error?e.message:"历史会话删除失败";}finally{busy.value=false;}}
function newChat():void{if(busy.value)return;conversationId.value='';transcript.value=[];answer.value='';citations.value=[];question.value='';error.value='';contextUsage.value=undefined;if(route.query.c)router.replace({query:{}});}
function sendOnEnter(event:KeyboardEvent):void{if(event.isComposing||event.keyCode===229)return;if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();void ask();}
let active:ChatStream|undefined;
function selectAvailableDataset(preferred=""):void{const available=datasets.readyDatasets;if(available.some(dataset=>dataset.id===preferred)){selectedId.value=preferred;return;}if(!available.some(dataset=>dataset.id===selectedId.value))selectedId.value=available[0]?.id??"";}
onMounted(async()=>{await datasets.load();selectAvailableDataset();try{conversations.value=await request("/conversations");}catch{error.value="会话列表加载失败";}const c=route.query.c;if(typeof c==="string"&&c)await resume(c);});
onActivated(async()=>{try{conversations.value=await request("/conversations");}catch{/* keep stale list */}});
onBeforeUnmount(()=>active?.cancel());
watch(()=>datasets.readyDatasets.map(dataset=>dataset.id).join(","),()=>selectAvailableDataset());

async function resume(id:string):Promise<void>{const row=conversations.value.find(c=>c.id===id);if(!row||busy.value)return;selectAvailableDataset(row.datasetId);await nextTick();conversationId.value=id;try{transcript.value=await request("/conversations/"+id+"/messages?datasetId="+encodeURIComponent(selectedId.value));}catch(e){error.value=e instanceof Error?e.message:"会话加载失败";}}

watch(selectedId,async(datasetId,previous)=>{if(!datasetId||datasetId===previous||busy.value)return;active?.cancel();conversationId.value="";transcript.value=[];answer.value="";citations.value=[];contextUsage.value=undefined;const latest=conversations.value.find(c=>c.datasetId===datasetId);if(latest)await resume(latest.id);});

async function ask():Promise<void>{if(!selectedId.value||!question.value.trim()||busy.value)return;if(questionTooLong.value){error.value="问题过长：服务端按 UTF-8 计算上限 8000 字节（约 2600 个汉字），请精简后重试。";return;}if(!conversationId.value){conversationId.value=randomUUID();router.replace({query:{c:conversationId.value}});}busy.value=true;error.value="";answer.value="";citations.value=[];contextUsage.value=undefined;phase.value="正在思考并检索…";const q=question.value;active=streamChat({datasetId:selectedId.value,question:q,conversationId:conversationId.value});let final=false;try{for await(const event of active.events){if(event.type==="context")contextUsage.value={estimatedTokens:event.estimatedTokens,usableTokens:event.usableTokens,evidenceCount:event.evidenceCount,evidenceLimit:event.evidenceLimit};if(event.type==="retrieval")phase.value="已检索到 "+event.hits.length+" 条证据，正在生成回答…";if(event.type==="token")answer.value+=event.text;if(event.type==="error")throw new Error(event.message);if(event.type==="final"){answer.value=event.answer;citations.value=event.citations;conversationId.value=event.conversationId??"";transcript.value.push({role:"user",content:q,citations:[]},{role:"assistant",content:answer.value,citations:citations.value});question.value="";answer.value="";citations.value=[];final=true;}}if(final)conversations.value=await request("/conversations");}catch(e){error.value=e instanceof Error&&e.name==="AbortError"?"已停止生成":e instanceof Error?e.message:"问答失败";}finally{busy.value=false;phase.value="";active=undefined;}}
</script>
<template>
  <main class="chat-page">
    <header class="page-heading chat-heading">
      <div><span class="eyebrow">ASK YOUR KNOWLEDGE</span><h1>与知识对话<span class="heading-dot">.</span></h1></div><HistoryDrawer
        :items="conversations"
        :busy="busy"
        :selected="conversationId"
        @select="resume"
        @new="newChat"
        @refresh="refreshHistory"
        @delete="deleteConversations"
      />
    </header><p
      v-if="datasets.error"
      role="alert"
    >
      {{ datasets.error }}
    </p><p
      v-if="error"
      role="alert"
    >
      {{ error }}
    </p><div
      v-if="!datasets.readyDatasets.length"
      class="chat-welcome"
    >
      <KnowledgeOrb /><h2>一个好答案，从你的知识开始。</h2><p>上传资料并完成索引后，就可以在这里展开对话。</p><RouterLink
        to="/datasets"
        class="button-link"
      >
        前往知识库 <AppIcon name="arrow" />
      </RouterLink>
    </div><template v-else>
      <label class="knowledge-picker"><AppIcon name="library" />知识库<select
        v-model="selectedId"
        :disabled="busy"
      ><option
        v-for="dataset in datasets.readyDatasets"
        :key="dataset.id"
        :value="dataset.id"
      >{{ dataset.name }}</option></select></label>
      <div
        v-if="!transcript.length && !busy"
        class="chat-welcome"
      >
        <KnowledgeOrb /><h2>今天，想从知识中发现什么？</h2><p>从一个问题开始，让资料里的线索彼此连接。</p><div class="prompt-chips">
          <button
            type="button"
            @click="question='请总结这些资料的核心观点'"
          >
            总结核心观点 <AppIcon name="arrow" />
          </button><button
            type="button"
            @click="question='这些资料中有哪些值得关注的细节？'"
          >
            发现关键细节 <AppIcon name="arrow" />
          </button>
        </div>
      </div>
      <article
        v-for="(message,index) in transcript"
        :key="index"
        class="chat-message"
        :class="'chat-message--'+message.role"
      >
        <h2><span class="message-avatar"><AppIcon :name="message.role==='user'?'chat':'spark'" /></span>{{ message.role==="user"?"你":"知识助手" }}</h2><MarkdownContent
          v-if="message.role==='assistant'"
          :content="message.content"
          :citations="message.citations"
        /><p
          v-else
          class="answer-content"
        >
          {{ message.content }}
        </p>
      </article>
      <p
        v-if="phase"
        class="thinking-status"
        role="status"
      >
        <span
          class="thinking-dots"
          aria-hidden="true"
        ><i /><i /><i /></span>{{ phase }}
      </p><MarkdownContent
        v-if="answer"
        :content="answer"
      /><p
        v-if="contextWarning"
        class="context-warning"
        role="status"
      >
        <AppIcon name="spark" />{{ contextWarning }}
      </p><form
        class="chat-composer"
        @submit.prevent="ask"
      >
        <label><span class="sr-only">问题</span><textarea
          v-model="question"
          maxlength="4000"
          placeholder="基于当前知识库提问；可随时切换知识库"
          rows="2"
          :disabled="busy"
          required
          @keydown.enter="sendOnEnter"
        /></label><div class="composer-footer">
          <small><AppIcon name="spark" />Enter 发送 · Ctrl / ⌘ + Enter 换行 · 上限 8000 字节{{ questionTooLong?"（已超长）":"" }}</small><button
            v-if="!busy"
            :disabled="!question.trim()||questionTooLong"
          >
            发送 <AppIcon name="send" />
          </button><button
            v-else
            type="button"
            @click="active?.cancel()"
          >
            停止生成
          </button>
        </div>
      </form>
    </template>
  </main>
</template>
