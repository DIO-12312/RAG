<script setup lang="ts">
import {onMounted,onActivated,onBeforeUnmount,ref,watch,nextTick} from "vue";import {streamChat,type ChatStream} from "@/api/chat";import {useDatasetStore} from "@/stores/datasets";import type {Citation} from "@/api/contracts";import {request} from "@/api/http";
import AppIcon from "@/components/AppIcon.vue";
import KnowledgeOrb from "@/components/KnowledgeOrb.vue";
import MarkdownContent from "@/components/MarkdownContent.vue";
import HistoryDrawer from "@/components/HistoryDrawer.vue";
defineOptions({name:'ChatView'});
const datasets=useDatasetStore();const selectedId=ref("");const question=ref("");const answer=ref("");const citations=ref<Citation[]>([]);const error=ref("");const phase=ref("");const busy=ref(false);const conversationId=ref("");const transcript=ref<{role:string;content:string;citations:Citation[]}[]>([]);const conversations=ref<{id:string;datasetId:string;title:string;updatedAt:string}[]>([]);
async function refreshHistory():Promise<void>{try{conversations.value=await request('/conversations');}catch(e){error.value=e instanceof Error?e.message:'历史会话加载失败';}}
function newChat():void{if(busy.value)return;conversationId.value='';transcript.value=[];answer.value='';citations.value=[];question.value='';error.value='';}
let active:ChatStream|undefined;let resuming=false;
onMounted(async()=>{await datasets.load();selectedId.value=datasets.readyDatasets[0]?.id??"";try{conversations.value=await request("/conversations");}catch{error.value="会话列表加载失败";}});
onActivated(async()=>{try{conversations.value=await request("/conversations");}catch{/* keep stale list */}});
onBeforeUnmount(()=>active?.cancel());watch(selectedId,()=>{if(resuming)return;active?.cancel();conversationId.value="";transcript.value=[];answer.value="";citations.value=[];});
async function resume(id:string):Promise<void>{const row=conversations.value.find(c=>c.id===id);if(!row||busy.value)return;resuming=true;selectedId.value=row.datasetId;await nextTick();resuming=false;conversationId.value=id;try{transcript.value=await request("/conversations/"+id+"/messages");}catch(e){error.value=e instanceof Error?e.message:"会话加载失败";}}
async function ask():Promise<void>{if(!selectedId.value||!question.value.trim()||busy.value)return;busy.value=true;error.value="";answer.value="";citations.value=[];phase.value="正在思考并检索…";const q=question.value;active=streamChat({datasetId:selectedId.value,question:q,conversationId:conversationId.value||undefined});let final=false;try{for await(const event of active.events){if(event.type==="retrieval")phase.value="已检索到 "+event.hits.length+" 条证据，正在生成回答…";if(event.type==="token")answer.value+=event.text;if(event.type==="error")throw new Error(event.message);if(event.type==="final"){answer.value=event.answer;citations.value=event.citations;conversationId.value=event.conversationId??"";transcript.value.push({role:"user",content:q,citations:[]},{role:"assistant",content:answer.value,citations:citations.value});question.value="";answer.value="";citations.value=[];final=true;}}if(final)conversations.value=await request("/conversations");}catch(e){error.value=e instanceof Error&&e.name==="AbortError"?"已停止生成":e instanceof Error?e.message:"问答失败";}finally{busy.value=false;phase.value="";active=undefined;}}
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
        /><p
          v-else
          class="answer-content"
        >
          {{ message.content }}
        </p><details
          v-for="citation in message.citations"
          :key="citation.ordinal"
        >
          <summary><AppIcon name="file" />[{{ citation.ordinal }}] {{ citation.evidence.sourceName }} · {{ citation.evidence.locator }}</summary><p class="answer-content">
            {{ citation.evidence.content }}
          </p>
        </details>
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
      /><form
        class="chat-composer"
        @submit.prevent="ask"
      >
        <label><span class="sr-only">问题</span><textarea
          v-model="question"
          maxlength="4000"
          placeholder="基于这个知识库提问"
          rows="2"
          :disabled="busy"
          required
        /></label><div class="composer-footer">
          <small><AppIcon name="spark" />答案将附上可追溯的来源</small><button
            v-if="!busy"
            :disabled="!question.trim()"
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
