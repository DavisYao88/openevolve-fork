<script setup>
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { api } from "../api";

const route = useRoute();
const data = ref({ nodes: [] });
const error = ref("");
const node = computed(() => data.value.nodes?.find((item) => item.id === route.params.id) || null);
const metadata = computed(() => (node.value?.metadata && typeof node.value.metadata === "object" ? node.value.metadata : {}));
const parentMetrics = computed(() => (metadata.value.parent_metrics && typeof metadata.value.parent_metrics === "object" ? metadata.value.parent_metrics : null));
const promptEntries = computed(() => Object.entries(node.value?.prompts || {}));
const artifactEntries = computed(() => {
  const raw = node.value?.artifacts_json;
  if (!raw) return [];
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    return Object.entries(parsed || {});
  } catch {
    return [["artifacts_json", raw]];
  }
});

onMounted(async () => {
  try {
    data.value = await api.evolution(route.query.run_id || "", route.query.checkpoint || "");
  } catch (cause) {
    error.value = cause.message;
  }
});
</script>

<template>
  <section>
    <div class="page-heading">
      <div><p class="eyebrow">Program</p><h1>程序详情</h1><p class="mono">{{ route.params.id }}</p></div>
      <RouterLink class="button" :to="{ path: '/', query: route.query }">← 返回可视化</RouterLink>
    </div>
    <p v-if="error" class="message error">{{ error }}</p>
    <div v-else-if="!node" class="card empty-state">没有找到该程序，可能对应的检查点已更新。</div>
    <div v-else class="program-content">
      <section class="card program-summary">
        <div><small>代数</small><strong>{{ node.generation ?? "—" }}</strong></div>
        <div><small>岛屿</small><strong>{{ node.island ?? "—" }}</strong></div>
        <div><small>发现轮次</small><strong>{{ node.iteration_found ?? "—" }}</strong></div>
        <div><small>语言</small><strong>{{ node.language || "python" }}</strong></div>
        <div><small>复杂度</small><strong>{{ Number(node.complexity ?? 0).toFixed(1) }}</strong></div>
        <div><small>多样性</small><strong>{{ Number(node.diversity ?? 0).toFixed(3) }}</strong></div>
        <div v-for="(value, key) in node.metrics || {}" :key="key"><small>{{ key }}</small><strong>{{ value }}</strong></div>
      </section>

      <section v-if="node.changes_description" class="card program-block">
        <h2>变更说明</h2>
        <p class="changes">{{ node.changes_description }}</p>
      </section>

      <section class="card program-block">
        <h2>程序代码</h2>
        <pre>{{ node.code || "无代码内容" }}</pre>
      </section>

      <section v-if="promptEntries.length" class="card program-block">
        <h2>提示词</h2>
        <article v-for="[name, value] in promptEntries" :key="name" class="prompt-entry">
          <h3>{{ name }}</h3>
          <pre>{{ typeof value === "string" ? value : JSON.stringify(value, null, 2) }}</pre>
        </article>
      </section>

      <section v-if="parentMetrics" class="card program-block">
        <h2>父程序指标</h2>
        <div class="metric-row">
          <span v-for="(value, key) in parentMetrics" :key="key"><small>{{ key }}</small><b>{{ value }}</b></span>
        </div>
      </section>

      <section v-if="artifactEntries.length || node.artifact_dir" class="card program-block">
        <h2>产物</h2>
        <p v-if="node.artifact_dir" class="artifact-dir mono">{{ node.artifact_dir }}</p>
        <article v-for="[name, value] in artifactEntries" :key="name" class="prompt-entry">
          <h3>{{ name }}</h3>
          <pre>{{ typeof value === "string" ? value : JSON.stringify(value, null, 2) }}</pre>
        </article>
      </section>
    </div>
  </section>
</template>

<style scoped>
.program-content { display:grid; gap:14px; }
.program-summary { display:flex; flex-wrap:wrap; gap:24px; padding:17px 20px; }
.program-summary > div { display:flex; min-width:100px; flex-direction:column; gap:5px; }
.program-summary small { color:var(--muted); font-size:10px; }
.program-summary strong { font-size:13px; }
.program-block { padding:19px; }
.program-block h2 { margin:0 0 12px; font-size:15px; }
.program-block pre { max-height:60vh; overflow:auto; margin:0; padding:16px; border-radius:9px; background:#172033; color:#d7e0f1; font:11px/1.6 "IBM Plex Mono",monospace; white-space:pre; }
.changes { margin:0; color:#57647a; font-size:12px; line-height:1.6; }
.prompt-entry { margin-bottom:14px; }
.prompt-entry:last-child { margin-bottom:0; }
.prompt-entry h3 { margin:0 0 6px; color:#57647a; font-size:11px; }
.metric-row { display:flex; flex-wrap:wrap; gap:8px; }
.metric-row span { display:flex; flex-direction:column; gap:3px; padding:7px 9px; border:1px solid var(--line); border-radius:7px; }
.metric-row small { color:var(--muted); font-size:10px; }
.metric-row b { font-size:11px; }
.artifact-dir { margin:0 0 12px; color:var(--muted); font-size:10px; overflow-wrap:anywhere; }
</style>
