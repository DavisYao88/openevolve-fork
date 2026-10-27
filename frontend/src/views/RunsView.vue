<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { api } from "../api";
import LaunchModal from "../components/LaunchModal.vue";

const runs = ref([]);
const loading = ref(true);
const error = ref("");
const showLaunch = ref(false);
const startedMessage = ref("");
let timer;

async function loadRuns() {
  try {
    error.value = "";
    const response = await api.runs();
    runs.value = response.runs || [];
  } catch (cause) {
    error.value = cause.message;
  } finally {
    loading.value = false;
  }
}

function onStarted(run) {
  showLaunch.value = false;
  startedMessage.value = `任务「${run?.name || run?.id}」已启动，正在后台运行。`;
  loadRuns();
}

function statusLabel(status) {
  return ({ completed: "已完成", running: "运行中", failed: "失败", interrupted: "已中断", unknown: "未知" })[status] || status;
}

function reportUrl(runId) {
  return `/api/runs/${encodeURIComponent(runId)}/report?format=html`;
}

function fmtScore(value) {
  return Number.isFinite(value) ? Number(value).toFixed(4) : "—";
}

onMounted(() => { loadRuns(); timer = window.setInterval(loadRuns, 5000); });
onUnmounted(() => window.clearInterval(timer));
</script>

<template>
  <section>
    <div class="page-heading">
      <div><p class="eyebrow">Evolution Runs</p><h1>任务列表</h1><p>查看由进化工作台创建的任务和结果。</p></div>
      <div class="heading-actions">
        <button class="button" @click="loadRuns">↻ 刷新</button>
        <button class="button primary" @click="showLaunch = true">＋ 新建演化</button>
      </div>
    </div>
    <p v-if="startedMessage" class="message">{{ startedMessage }}</p>
    <p v-if="error" class="message error">{{ error }}</p>
    <div v-if="loading" class="card empty-state">正在读取任务…</div>
    <div v-else-if="!runs.length" class="card empty-state">还没有进化任务。<br><button class="button primary create-link" @click="showLaunch = true">新建进化任务</button></div>
    <div v-else class="runs-table card">
      <div class="table-head"><span>任务</span><span>状态</span><span>模型</span><span>轮数 / 程序</span><span>最高分</span><span>更新时间</span><span></span></div>
      <article v-for="run in runs" :key="run.id" class="run-row">
        <div class="run-name">
          <RouterLink class="run-link" :to="{ path: `/runs/${run.id}` }">{{ run.name || run.id }}</RouterLink>
          <small class="mono">{{ run.run_dir }}</small>
          <small v-if="run.template_name" class="run-template">模板：{{ run.template_name }}</small>
        </div>
        <span class="status-badge" :class="run.status">{{ statusLabel(run.status) }}</span>
        <span class="run-cell">{{ run.model || "—" }}</span>
        <span class="run-cell">{{ run.finished_iterations ?? 0 }} / {{ run.programs }}</span>
        <span class="run-cell mono">{{ fmtScore(run.best_metrics?.combined_score) }}</span>
        <span class="run-cell">{{ run.updated_at || "—" }}</span>
        <div class="row-actions">
          <RouterLink class="button open-run" :to="{ path: `/runs/${run.id}` }">详情</RouterLink>
          <a v-if="run.has_report" class="button open-run" :href="reportUrl(run.id)" target="_blank" rel="noopener">报告</a>
          <RouterLink class="button open-run" :to="{ path: '/code', query: { run_id: run.id } }">代码</RouterLink>
          <RouterLink class="button primary open-run" :to="{ path: '/', query: { run_id: run.id } }">查看</RouterLink>
        </div>
      </article>
    </div>

    <LaunchModal v-if="showLaunch" @close="showLaunch = false" @started="onStarted" />
  </section>
</template>

<style scoped>
.runs-table { overflow:hidden; }
.table-head, .run-row { display:grid; grid-template-columns:minmax(180px,1.5fr) 90px minmax(100px,1fr) 100px 110px 150px 196px; align-items:center; gap:14px; padding:13px 16px; }
.table-head { background:#f8f9fc; color:#78849a; font-size:10px; font-weight:700; }
.run-row { min-height:67px; border-top:1px solid #edf0f5; font-size:11px; }
.run-name { display:flex; min-width:0; flex-direction:column; gap:5px; }
.run-name strong, .run-link { overflow:hidden; text-overflow:ellipsis; font-size:13px; }
.run-link { color:inherit; text-decoration:none; }
.run-link:hover { color:var(--accent); text-decoration:underline; }
.row-actions { display:flex; gap:6px; }
.run-name small { overflow:hidden; color:var(--muted); font-size:9px; text-overflow:ellipsis; white-space:nowrap; }
.run-template { color:#7d8ba3; }
.run-cell { overflow:hidden; color:#58667d; text-overflow:ellipsis; white-space:nowrap; }
.status-badge { width:max-content; padding:5px 8px; border-radius:100px; background:#eef1f5; color:#68748a; font-size:9px; font-weight:700; white-space:nowrap; }
.status-badge.completed { background:#e7f7ef; color:#178454; }.status-badge.running { background:#e8efff; color:#315fce; }.status-badge.failed { background:#fff0f0; color:#c14242; }.status-badge.interrupted { background:#fff5dd; color:#946407; }
.open-run { min-height:30px; padding:4px 9px; font-size:10px; }
.create-link { margin-top:14px; }
.heading-actions { display:flex; gap:8px; }
@media(max-width:1000px) {
  .table-head { display:none; }
  .run-row { grid-template-columns:minmax(0,1fr) auto; gap:10px; }
  .run-name { grid-column:1; }.run-row .status-badge { grid-column:2; grid-row:1; }
  .run-cell { grid-column:1; }.open-run { grid-column:2; grid-row:4; }
}
</style>
