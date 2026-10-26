<script setup>
import { computed, onMounted, ref } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";
import { theme as antdTheme } from "ant-design-vue";

const route = useRoute();
const darkMode = ref(false);

// Ant Design Vue 跟随页面深浅色，圆角/主色对齐 style.css 里的设计变量
const themeConfig = computed(() => ({
  algorithm: darkMode.value ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
  token: {
    colorPrimary: "#385dcc",
    borderRadius: 8,
    fontSize: 12,
    controlHeight: 34,
  },
}));

function applyTheme(enabled) {
  darkMode.value = enabled;
  document.documentElement.dataset.theme = enabled ? "dark" : "light";
  localStorage.setItem("openevolve.theme", enabled ? "dark" : "light");
}

// 设置页里切换主题后，通知顶部同步（页面不会刷新，只改 data-theme）
function syncTheme() {
  darkMode.value = document.documentElement.dataset.theme === "dark";
}

onMounted(() => {
  applyTheme(localStorage.getItem("openevolve.theme") === "dark");
  window.addEventListener("openevolve-theme", syncTheme);
});
</script>

<template>
  <a-config-provider :theme="themeConfig">
  <div class="app-shell">
    <header class="app-header">
      <RouterLink class="brand" to="/runs">
        <span class="brand-mark">O</span>
        <span>
          <strong>OpenEvolve</strong>
          <small>进化工作台</small>
        </span>
      </RouterLink>
      <nav class="main-nav" aria-label="主导航">
        <RouterLink to="/runs" :class="{ active: route.path.startsWith('/runs') }">任务列表</RouterLink>
        <RouterLink to="/manual" :class="{ active: route.path === '/manual' }">人工反馈</RouterLink>
      </nav>
      <div class="header-actions">
        <RouterLink class="icon-button" :to="'/settings'" :class="{ active: route.path === '/settings' }" title="设置" aria-label="设置">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="3.2"></circle>
            <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.11a1.7 1.7 0 0 0-1.11-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.11A1.7 1.7 0 0 0 4.67 9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.67h.08A1.7 1.7 0 0 0 10.11 3.1V3a2 2 0 1 1 4 0v.11a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.08a1.7 1.7 0 0 0 1.56 1.03H21a2 2 0 1 1 0 4h-.11a1.7 1.7 0 0 0-1.49.86Z"></path>
          </svg>
        </RouterLink>
      </div>
    </header>
    <main class="app-content">
      <RouterView />
    </main>
  </div>
  </a-config-provider>
</template>
