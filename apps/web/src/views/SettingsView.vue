<script setup lang="ts">
import {onMounted,ref} from "vue";
import {getSettings,updateAgentSettings,updateChatSettings,updateEmbeddingSettings,updateRerankSettings} from "@/api/settings";
import type {SettingsResponse} from "@/api/contracts";
import {useAuthStore} from "@/stores/auth";import {useRouter} from "vue-router";
import AppIcon from "@/components/AppIcon.vue";
const auth=useAuthStore();const router=useRouter();const settings=ref<SettingsResponse>();const error=ref("");const notice=ref("");const keys=ref({chat:"",embedding:"",rerank:""});const busy=ref(false);
const rerankVisible = ref(false);
onMounted(async()=>{try{settings.value=await getSettings();rerankVisible.value=settings.value.rerankEnabled;}catch(e){error.value=e instanceof Error?e.message:"配置加载失败";}});
async function save(kind:"chat"|"embedding"|"rerank"):Promise<void>{if(!settings.value||busy.value)return;busy.value=true;error.value="";notice.value="";try{let result:SettingsResponse;if(kind==="chat")result=await updateChatSettings({...settings.value.chat,apiKey:keys.value.chat||undefined});else if(kind==="embedding")result=await updateEmbeddingSettings({...settings.value.embedding,apiKey:keys.value.embedding||undefined});else {result=await updateRerankSettings({...settings.value.rerank,apiKey:keys.value.rerank||undefined});settings.value=result;keys.value.rerank="";result=await updateAgentSettings({rerankEnabled:true});}settings.value=result;keys.value[kind]="";notice.value=kind==="rerank"?"Rerank 配置已保存并启用":"已保存";}catch(e){error.value=e instanceof Error?e.message:"保存失败";}finally{busy.value=false;}}
async function toggleRerank(): Promise<void> {
  if (!settings.value || busy.value) return;
  error.value = "";
  notice.value = "";
  if (!rerankVisible.value) {
    rerankVisible.value = true;
    notice.value = "填写并保存配置后，Rerank 将用于后续问答。";
    return;
  }
  busy.value = true;
  try {
    const result = await updateAgentSettings({rerankEnabled: false});
    settings.value.rerankEnabled = result.rerankEnabled;
    rerankVisible.value = false;
    keys.value.rerank = "";
    notice.value = "Rerank 已关闭，已保存的配置会保留。";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "关闭失败，请重试";
  } finally { busy.value = false; }
}
async function logout():Promise<void>{try{await auth.logout();await router.push("/login");}catch(e){error.value=e instanceof Error?e.message:"退出失败";}}
</script>
<template>
  <main class="settings-page">
    <header class="page-heading">
      <span class="eyebrow">MAKE IT YOURS</span><h1>偏好设置<span class="heading-dot">.</span></h1><p>连接你的模型，打造顺手的知识空间。</p>
    </header><p class="settings-account">
      {{ auth.user?.email }} <button @click="logout">
        退出登录
      </button>
    </p><p
      v-if="error"
      role="alert"
    >
      {{ error }}
    </p><p
      v-if="notice"
      role="status"
    >
      {{ notice }}
    </p><template v-if="settings">
      <article class="settings-card">
        <div class="settings-card-heading">
          <span class="icon-tile"><AppIcon name="chat" /></span><div><h2>对话模型</h2><span class="eyebrow">CHAT MODEL</span></div>
        </div><p>使用支持工具调用的 OpenAI 兼容接口。Base URL 通常以 /v1 结尾。</p><form @submit.prevent="save('chat')">
          <label>Base URL<input
            v-model="settings.chat.baseUrl"
            type="url"
            required
            placeholder="https://api.example.com/v1"
          ></label>
          <label>模型名称<input
            v-model="settings.chat.modelName"
            required
          ></label>
          <label>API Key · {{ settings.chat.apiKeyHint || "未配置" }}<input
            v-model="keys.chat"
            type="password"
            autocomplete="new-password"
            placeholder="留空保留原密钥"
          ></label>
          <label>超时（秒）<input
            v-model.number="settings.chat.timeoutSeconds"
            type="number"
            min="1"
            max="300"
            required
          ></label>
          <label><input
            v-model="settings.chat.thinkingEnabled"
            type="checkbox"
          >启用思考模式（需模型支持）</label><button :disabled="busy">
            保存对话模型
          </button>
        </form>
      </article>
      <article class="settings-card">
        <div class="settings-card-heading">
          <span class="icon-tile icon-tile--mint"><AppIcon name="library" /></span><div><h2>Embedding 模型</h2><span class="eyebrow">RETRIEVAL</span></div>
        </div><p>配置加密保存到数据库，新建知识库使用此配置。已有知识库固定模型快照，避免混用向量。当前索引支持 1024 维。</p><form @submit.prevent="save('embedding')">
          <label>Base URL<input
            v-model="settings.embedding.baseUrl"
            type="url"
            required
            placeholder="https://api.example.com/v1"
          ></label>
          <label>模型名称<input
            v-model="settings.embedding.modelName"
            required
          ></label>
          <label>API Key · {{ settings.embedding.apiKeyHint || "未配置" }}<input
            v-model="keys.embedding"
            type="password"
            autocomplete="new-password"
            placeholder="留空保留原密钥"
          ></label>
          <label>超时（秒）<input
            v-model.number="settings.embedding.timeoutSeconds"
            type="number"
            min="1"
            max="300"
            required
          ></label>
          <label>检索 Top-K<input
            v-model.number="settings.embedding.defaultTopK"
            type="number"
            min="1"
            max="30"
            required
          ></label><button :disabled="busy">
            保存 Embedding 配置
          </button>
        </form>
      </article>
      <article class="settings-card">
        <div class="settings-card-heading">
          <span class="icon-tile icon-tile--peach"><AppIcon name="settings" /></span><div><h2>Rerank 模型</h2><span class="eyebrow">RERANK</span></div>
        </div><p>启用后对检索候选进行相关性重排；关闭时保留配置。</p>
        <button
          type="button"
          role="switch"
          :aria-checked="rerankVisible"
          :disabled="busy"
          @click="toggleRerank"
        >
          {{ rerankVisible ? "关闭 Rerank" : "启用 Rerank" }}
        </button>
        <form
          v-if="rerankVisible"
          @submit.prevent="save('rerank')"
        >
          <p>{{ settings.rerankEnabled ? "已启用" : "尚未生效：保存配置后启用" }}。使用专用 /rerank 接口，Base URL 可填写到 /v1 或完整 /rerank 地址。</p>
          <label>Base URL<input
            v-model="settings.rerank.baseUrl"
            type="url"
            required
          ></label><label>模型名称<input
            v-model="settings.rerank.modelName"
            required
          ></label><label>API Key · {{ settings.rerank.apiKeyHint || "未配置" }}<input
            v-model="keys.rerank"
            type="password"
            autocomplete="new-password"
            placeholder="留空保留原密钥"
          ></label><label>超时（秒）<input
            v-model.number="settings.rerank.timeoutSeconds"
            type="number"
            min="1"
            max="300"
            required
          ></label><label>Top-N（重排后保留数量）<input
            v-model.number="settings.rerank.topN"
            type="number"
            min="1"
            max="20"
            required
          ></label><button :disabled="busy">
            保存并启用 Rerank
          </button>
        </form>
      </article>
    </template><p v-else>
      加载中…
    </p>
  </main>
</template>
