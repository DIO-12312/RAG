<script setup lang="ts">
import LocaleSwitcher from "@/components/LocaleSwitcher.vue";
import { messages } from "@/i18n/messages";
import { useLocaleStore } from "@/stores/locale";
import { useAuthStore } from "@/stores/auth";
import AppIcon from "@/components/AppIcon.vue";
import { buildCommit, shortCommit } from "@/utils/version";

const locale = useLocaleStore();
const auth = useAuthStore();
const version = shortCommit();
</script>

<template>
  <div class="app-shell">
    <aside class="app-shell__sidebar">
      <header class="app-shell__header">
        <RouterLink
          to="/"
          class="brand"
          aria-label="RAG MVP"
        >
          <span class="brand-mark"><AppIcon name="spark" /></span><strong>RAG<span>MVP</span></strong>
        </RouterLink>
      </header>
      <p class="nav-caption">
        {{ locale.value === 'zh-CN' ? '个人工作空间' : 'WORKSPACE' }}
      </p><nav aria-label="Primary navigation">
        <RouterLink to="/">
          <AppIcon name="home" />
          {{ messages[locale.value].overview }}
        </RouterLink>
        <RouterLink to="/datasets">
          <AppIcon name="library" />
          {{ messages[locale.value].datasets }}
        </RouterLink>
        <RouterLink to="/chat">
          <AppIcon name="chat" />
          {{ messages[locale.value].chat }}
        </RouterLink>
        <RouterLink to="/settings">
          <AppIcon name="settings" />
          {{ messages[locale.value].settings }}
        </RouterLink>
      </nav>
      <div class="sidebar-note">
        <AppIcon name="shield" /><p>{{ locale.value === 'zh-CN' ? '你的知识，你的空间' : 'Your knowledge. Your space.' }}<small>{{ locale.value === 'zh-CN' ? '让每一个答案，都有据可循。' : 'Answers grounded in your sources.' }}</small></p>
      </div>
      <footer class="sidebar-account">
        <span class="account-avatar">{{ auth.user?.email?.[0]?.toUpperCase() || 'U' }}</span><span class="account-copy"><strong>{{ locale.value === 'zh-CN' ? '个人账号' : 'Personal account' }}</strong><small>{{ auth.user?.email || 'RAG Workspace' }}</small></span><LocaleSwitcher />
      </footer>
      <p
        class="build-version"
        :title="`${locale.value === 'zh-CN' ? '构建版本' : 'Build revision'} ${buildCommit}`"
      >
        {{ locale.value === 'zh-CN' ? '版本' : 'Build' }} {{ version }}
      </p>
    </aside>
    <section class="app-shell__content">
      <slot />
    </section>
  </div>
</template>
