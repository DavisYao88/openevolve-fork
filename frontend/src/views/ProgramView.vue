<script setup>
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { api } from "../api";

const route = useRoute();
const data = ref({ nodes: [] });
const error = ref("");
const node = computed(() => data.value.nodes?.find((item) => item.id === route.params.id));

onMounted(async () => {
  try {
    data.value = await api.evolution(route.query.run_id || "");
  } catch (cause) {
    error.value = cause.message;
  }
});
</script>

<template>
  <section>
    <div class="page-heading"><div><p class="eyebrow">Program</p><h1>程序详情</h1><p class="mono">{{ route.params.id }}</p></div><RouterLink class="button" :to="{ path: '/', query: route.query }">← 返回可视化</RouterLink></div>
    <p v-if="error" class="message error">{{ error }}</p>
    <div v-else-if="!node" class="card empty-state">没有找到该程序，可能对应的检查点已更新。</div>
    <div v-else class="program-content">
      <section class="card program-summary">
        <div><small>代数</small><strong>{{ node.generation ?? "—" }}</strong></div>
        <div><small>岛屿</small><strong>{{ node.island ?? "—" }}</strong></div>
        <div v-for="(value, key) in node.metrics || {}" :key="key"><small>{{ key }}</small><strong>{{ value }}</strong></div>
      </section>
      <section class="card program-code"><h2>程序代码</h2><pre>{{ node.code || "无代码内容" }}</pre></section>
      <section v-if="node.prompts || node.artifacts_json" class="card program-code"><h2>提示词与产物</h2><pre>{{ JSON.stringify(node.prompts || node.artifacts_json, null, 2) }}</pre></section>
    </div>
  </section>
</template>

<style scoped>
.program-content { display:grid; gap:14px; }
.program-summary { display:flex; flex-wrap:wrap; gap:24px; padding:17px 20px; }
.program-summary > div { display:flex; min-width:100px; flex-direction:column; gap:5px; }
.program-summary small { color:var(--muted); font-size:10px; }.program-summary strong { font-size:13px; }
.program-code { padding:19px; }.program-code h2 { margin:0 0 12px; font-size:15px; }
.program-code pre { max-height:70vh; overflow:auto; margin:0; padding:16px; border-radius:9px; background:#172033; color:#d7e0f1; font:11px/1.6 "IBM Plex Mono",monospace; white-space:pre; }
</style>
