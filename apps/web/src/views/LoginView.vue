<script setup lang="ts">
import {ref} from "vue";import {useRouter} from "vue-router";import {useAuthStore} from "@/stores/auth";import {register} from "@/api/auth";
const email=ref("");const password=ref("");const error=ref("");const busy=ref(false);const registering=ref(false);const auth=useAuthStore();const router=useRouter();
async function submit():Promise<void>{if(busy.value)return;busy.value=true;error.value="";try{if(registering.value){auth.user=await register({email:email.value,password:password.value});auth.expirationNotice="";}else await auth.login({email:email.value,password:password.value});password.value="";await router.push("/");}catch(e){error.value=e instanceof Error?e.message:"请求失败";}finally{busy.value=false;}}
</script>
<template>
  <main class="login-panel">
    <span class="eyebrow">RAG / PERSONAL WORKSPACE</span><h1>{{ registering?"创建账号":"欢迎回来" }}<span class="heading-dot">.</span></h1><p>登录你的个人知识库，让灵感有所依。</p><p v-if="auth.expirationNotice">
      {{ auth.expirationNotice }}
    </p><form @submit.prevent="submit">
      <label>邮箱<input
        v-model="email"
        type="email"
        autocomplete="email"
        required
      ></label><label>密码<input
        v-model="password"
        type="password"
        :autocomplete="registering?'new-password':'current-password'"
        minlength="8"
        maxlength="128"
        required
      ></label><button :disabled="busy">
        {{ busy?"处理中…":registering?"注册并登录":"登录" }}
      </button><p
        v-if="error"
        role="alert"
      >
        {{ error }}
      </p><button
        type="button"
        @click="registering=!registering"
      >
        {{ registering?"已有账号，前往登录":"没有账号？注册" }}
      </button>
    </form>
  </main>
</template>
