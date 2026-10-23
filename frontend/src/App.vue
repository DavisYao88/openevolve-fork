<script setup>
import { onMounted, ref } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";

const route = useRoute();
const darkMode = ref(false);

function applyTheme(enabled) {
  darkMode.value = enabled;
  document.documentElement.dataset.theme = enabled ? "dark" : "light";
  localStorage.setItem("openevolve.theme", enabled ? "dark" : "light");
}

onMounted(() => {
  applyTheme(localStorage.getItem("openevolve.theme") === "dark");
});
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <RouterLink class="brand" to="/">
        <span class="brand-mark">O</span>
        <span>
          <strong>OpenEvolve</strong>
          <small>进化工作台</small>
        </span>
      </RouterLink>
      <nav class="main-nav" aria-label="主导航">
        <RouterLink to="/" :class="{ active: route.path === '/' || route.path.startsWith('/program/') }">
          可视化
        </RouterLink>
        <RouterLink to="/manual" :class="{ active: route.path === '/manual' }">人工反馈</RouterLink>
        <RouterLink to="/launch" :class="{ active: route.path === '/launch' }">新建进化</RouterLink>
        <RouterLink to="/runs" :class="{ active: route.path === '/runs' }">任务列表</RouterLink>
      </nav>
      <button class="theme-button" type="button" :aria-pressed="darkMode" @click="applyTheme(!darkMode)">
        {{ darkMode ? "☾ 深色" : "☀ 浅色" }}
      </button>
      <a class="backend-status" href="http://127.0.0.1:8080" target="_blank" rel="noreferrer">
        <i></i> Flask API
      </a>
    </header>
    <main class="app-content">
      <RouterView />
    </main>
  </div>
</template>
