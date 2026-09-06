<script setup lang="ts">
import KnowledgeOrb from "@/components/KnowledgeOrb.vue";
import AppIcon from "@/components/AppIcon.vue";
</script>
<template>
  <main class="overview-page">
    <section class="welcome-banner">
      <div class="welcome-copy">
        <span class="eyebrow">YOUR PERSONAL KNOWLEDGE SPACE</span><h1>让知识连接，<br>让灵感发生。</h1><p>欢迎回来。把散落的资料汇聚于此，<br>让每一个好问题，都找到有依据的答案。</p><RouterLink
          class="button-link"
          to="/datasets"
        >
          探索我的知识库 <AppIcon name="arrow" />
        </RouterLink>
      </div><KnowledgeOrb />
    </section>
    <div class="section-heading">
      <h2>从这里开始</h2><span>从资料到答案，简单三步</span>
    </div>
    <div class="feature-grid">
      <RouterLink
        to="/datasets"
        class="feature-card"
      >
        <span class="feature-number">01 / COLLECT</span><span class="icon-tile"><AppIcon name="library" /></span><h2>给知识一个家</h2><p>按主题整理文档，创建属于自己的知识库。</p><span class="text-link">管理知识库 <AppIcon name="arrow" /></span>
      </RouterLink>
      <RouterLink
        to="/chat"
        class="feature-card"
      >
        <span class="feature-number">02 / EXPLORE</span><span class="icon-tile icon-tile--peach"><AppIcon name="chat" /></span><h2>与知识展开对话</h2><p>提出问题、连接线索，从你的资料中发现答案。</p><span class="text-link">开始对话 <AppIcon name="arrow" /></span>
      </RouterLink>
      <RouterLink
        to="/settings"
        class="feature-card"
      >
        <span class="feature-number">03 / PERSONALIZE</span><span class="icon-tile icon-tile--mint"><AppIcon name="settings" /></span><h2>找到适合你的模型</h2><p>连接对话模型，调整你的个人问答体验。</p><span class="text-link">模型设置 <AppIcon name="arrow" /></span>
      </RouterLink>
    </div>
  </main>
</template>
