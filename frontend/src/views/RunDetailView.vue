<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { api } from "../api";

const route = useRoute();
const runId = computed(() => route.params.id || "");

const detail = ref(null);
const checkpoints = ref([]);
const loading = ref(true);
const error = ref("");
const actionMessage = ref("");
const acting = ref(false);
const logText = ref("");
const logSize = ref(0);
const logEof = ref(true);
const logPanel = ref(null);
const autoScroll = ref(true);
let logOffset = 0;
let detailTimer;
let logTimer;

const statusLabels = { running: "运行中", completed: "已完成", failed: "失败", interrupted: "已中断", unknown: "未知" };
const stageLabels = { llm: "模型生成", diff: "差异应用", evaluation: "评测" };

const progress = computed(() => detail.value || {});
const progressPercent = computed(() => {
  const value = progress.value;
  if (!value) return 0;
  if (Number.isFinite(value.percent)) return value.percent;
  const total = value.total_iterations || 0;
  return total ? Math.min(100, ((value.finished_iterations || 0) / total) * 100) : 0;
});
const iterations = computed(() => {
  const list = detail.value?.iteration_details ?? detail.value?.iterations;
  return Array.isArray(list) ? list : [];
});
const lastIteration = computed(() => (iterations.value.length ? iterations.value[iterations.value.length - 1] : null));
const inputFiles = computed(() => detail.value?.input_files || {});
const isRunning = computed(() => progress.value.status === "running");
const bestScore = computed(() => detail.value?.best_metrics?.combined_score ?? null);
const resumedFrom = computed(() => detail.value?.resumed_from || "");
const plannedIterations = computed(() => detail.value?.planned_iterations ?? detail.value?.total_iterations ?? 0);
const templateLabel = computed(() => detail.value?.template_name || detail.value?.template || "自定义");

function programLink(id) {
  return { path: `/program/${encodeURIComponent(id)}`, query: { run_id: runId.value } };
}

function durationText(item) {
  return Number.isFinite(item?.duration_seconds) ? `${item.duration_seconds.toFixed(1)}s` : "—";
}

function tokenText(item) {
  const tokens = item?.tokens || {};
  return `${tokens.total ?? 0}`;
}

async function loadDetail() {
  try {
    error.value = "";
    const response = await api.runDetail(runId.value);
    detail.value = response.run || null;
  } catch (cause) {
    error.value = cause.message;
  } finally {
    loading.value = false;
  }
}

async function loadCheckpoints() {
  try {
    checkpoints.value = (await api.runCheckpoints(runId.value)).checkpoints || [];
  } catch {
    checkpoints.value = [];
  }
}

async function loadLog() {
  if (!runId.value) return;
  try {
    const response = await api.runLog(runId.value, logOffset);
    logText.value = logOffset === 0 ? response.log : logText.value + response.log;
    logOffset = response.offset;
    logSize.value = response.size;
    logEof.value = response.eof;
  } catch (cause) {
    error.value = cause.message;
  }
}

async function cancelRun() {
  acting.value = true;
  actionMessage.value = "";
  try {
    await api.cancelRun(runId.value);
    actionMessage.value = "已发送终止信号，任务会被标记为已中断。";
    await loadDetail();
  } catch (cause) {
    actionMessage.value = cause.message;
  } finally {
    acting.value = false;
  }
}

async function exportRun() {
  acting.value = true;
  actionMessage.value = "";
  try {
    const response = await api.exportRun(runId.value);
    actionMessage.value = `静态页面已生成：${response.index}（需用 HTTP 服务打开，例如 python3 -m http.server --directory ${response.static_dir} 8080）`;
  } catch (cause) {
    actionMessage.value = cause.message;
  } finally {
    acting.value = false;
  }
}

function openCheckpoint(name) {
  return { path: "/", query: { run_id: runId.value, checkpoint: name } };
}

const preview = ref(null);

async function previewFile(name) {
  preview.value = { name, content: "", loading: true, truncated: false, size: 0, error: "" };
  try {
    const response = await api.runInputFile(runId.value, name);
    preview.value = { name, ...response, loading: false, error: "" };
  } catch (cause) {
    preview.value = { name, content: "", loading: false, truncated: false, size: 0, error: cause.message };
  }
}

function closePreview() {
  preview.value = null;
}

watch(logText, async () => {
  if (!autoScroll.value) return;
  await nextTick();
  const panel = logPanel.value;
  if (panel) panel.scrollTop = panel.scrollHeight;
});

onMounted(() => {
  loadDetail();
  loadCheckpoints();
  loadLog();
  detailTimer = window.setInterval(loadDetail, 5000);
  logTimer = window.setInterval(() => { if (isRunning.value || !logEof.value) loadLog(); }, 3000);
});
onUnmounted(() => {
  window.clearInterval(detailTimer);
  window.clearInterval(logTimer);
});
</script>

<template>
  <section>
    <div class="page-heading">
      <div>
        <p class="eyebrow">Run Detail</p>
        <h1>{{ detail?.name || runId }}</h1>
        <p class="mono">{{ runId }}</p>
      </div>
      <div class="detail-actions">
        <button class="button" :disabled="acting || !isRunning" @click="cancelRun">■ 停止运行</button>
        <button class="button" :disabled="acting" @click="exportRun">⤓ 静态导出</button>
        <RouterLink class="button primary" :to="{ path: '/', query: { run_id: runId } }">查看可视化</RouterLink>
      </div>
    </div>

    <p v-if="error" class="message error">{{ error }}</p>
    <p v-else-if="actionMessage" class="message">{{ actionMessage }}</p>
    <div v-if="loading" class="card empty-state">正在读取任务详情…</div>

    <template v-else-if="detail">
      <section class="card run-progress">
        <div class="progress-head">
          <div>
            <p class="eyebrow">Progress</p>
            <h2>运行状态</h2>
          </div>
          <span class="status-badge" :class="detail.status">{{ statusLabels[detail.status] || detail.status }}</span>
        </div>
        <div class="progress-bar-track" role="progressbar" :aria-valuenow="progressPercent" aria-valuemin="0" aria-valuemax="100">
          <div class="progress-bar-fill" :class="detail.status" :style="{ width: `${progressPercent}%` }"></div>
        </div>
        <div class="progress-stats">
          <div><small>进度</small><strong>{{ detail.finished_iterations ?? 0 }} / {{ detail.total_iterations ?? detail.iterations ?? 0 }}</strong></div>
          <div><small>成功</small><strong>{{ detail.completed_iterations ?? 0 }}</strong></div>
          <div><small>失败</small><strong>{{ detail.failed_iterations ?? 0 }}</strong></div>
          <div><small>程序数</small><strong>{{ detail.programs ?? 0 }}</strong></div>
          <div><small>最高分</small><strong>{{ bestScore === null ? "—" : Number(bestScore).toFixed(4) }}</strong></div>
          <div><small>token</small><strong>{{ detail.tokens?.prompt || 0 }} / {{ detail.tokens?.completion || 0 }}</strong></div>
        </div>
        <p v-if="lastIteration" class="progress-last">
          最近一轮：第 {{ lastIteration.iteration }} 轮 ·
          <b>{{ lastIteration.status === "completed" ? "成功" : "失败" }}</b>
          <template v-if="lastIteration.stage"> · 阶段 {{ stageLabels[lastIteration.stage] || lastIteration.stage }}</template>
          <template v-if="lastIteration.error"> · {{ lastIteration.error }}</template>
        </p>
      </section>

      <div class="detail-grid">
        <section class="card">
          <h2 class="card-title">任务信息</h2>
          <dl class="info-list">
            <div><dt>模板</dt><dd>{{ templateLabel }}</dd></div>
            <div><dt>模型</dt><dd class="mono">{{ detail.model || "—" }}</dd></div>
            <div><dt>计划轮数</dt><dd>{{ plannedIterations || "—" }}</dd></div>
            <div><dt>输出目录</dt><dd class="mono">{{ detail.output_dir || "—" }}</dd></div>
            <div v-if="resumedFrom"><dt>续跑来源</dt><dd class="mono">{{ resumedFrom }}</dd></div>
            <div><dt>更新时间</dt><dd>{{ detail.updated_at || "—" }}</dd></div>
          </dl>
        </section>

        <section class="card">
          <h2 class="card-title">检查点</h2>
          <p v-if="!checkpoints.length" class="empty-hint">还没有检查点。检查点按固定轮次间隔保存。</p>
          <ul v-else class="checkpoint-list">
            <li v-for="item in checkpoints" :key="item.name">
              <div>
                <strong class="mono">{{ item.name }}</strong>
                <small>{{ item.programs }} 个程序 · {{ item.updated_at }}</small>
              </div>
              <RouterLink class="button" :to="openCheckpoint(item.name)">查看</RouterLink>
            </li>
          </ul>
        </section>

        <section class="card">
          <h2 class="card-title">输入文件</h2>
          <ul class="file-list">
            <li v-for="(exists, name) in inputFiles" :key="name">
              <span class="mono">{{ name }}</span>
              <span class="file-state" :class="exists ? 'ok' : 'missing'">{{ exists ? "已保存" : "缺失" }}</span>
            </li>
          </ul>
        </section>
      </div>

      <section class="card">
        <h2 class="card-title">每轮明细
          <span class="log-size">共 {{ iterations.length }} 轮</span>
        </h2>
        <p v-if="!iterations.length" class="empty-hint">日志里还没有解析到完成的轮次。</p>
        <div v-else class="table-scroll">
          <table class="iteration-table">
            <thead>
              <tr><th>轮次</th><th>状态</th><th>程序</th><th>父程序</th><th>耗时</th><th>token</th><th>指标</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in iterations" :key="item.iteration">
                <td>{{ item.iteration }}</td>
                <td>
                  <span class="iteration-state" :class="item.status">{{ item.status === "completed" ? "成功" : "失败" }}</span>
                </td>
                <td class="mono">
                  <RouterLink v-if="item.program_id" :to="programLink(item.program_id)">{{ item.program_id.slice(0, 8) }}</RouterLink>
                  <span v-else>—</span>
                </td>
                <td class="mono">
                  <RouterLink v-if="item.parent_id" :to="programLink(item.parent_id)">{{ item.parent_id.slice(0, 8) }}</RouterLink>
                  <span v-else>—</span>
                </td>
                <td>{{ durationText(item) }}</td>
                <td>{{ tokenText(item) }}</td>
                <td class="mono metrics-cell">{{ item.metrics_text || item.error || "—" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="card log-card">
        <h2 class="card-title">运行日志
          <span class="log-size">{{ (logSize / 1024).toFixed(1) }} KB</span>
          <label class="log-scroll"><input v-model="autoScroll" type="checkbox">自动滚动</label>
        </h2>
        <pre ref="logPanel" class="log-view">{{ logText || "暂无日志" }}</pre>
      </section>
    </template>
  </section>
</template>

<style scoped>
.detail-actions { display:flex; flex-wrap:wrap; gap:8px; }
.run-progress { padding:16px 18px; margin-bottom:16px; }
.card-title { margin:0 0 12px; font-size:14px; }
.progress-head { display:flex; align-items:center; justify-content:space-between; gap:14px; }
.progress-head h2 { margin:2px 0 0; font-size:14px; }
.status-badge { padding:5px 10px; border-radius:100px; background:#eef1f5; color:#68748a; font-size:10px; font-weight:700; }
.status-badge.completed { background:#e7f7ef; color:#178454; }
.status-badge.running { background:#e8efff; color:#315fce; }
.status-badge.failed { background:#fff0f0; color:#c14242; }
.status-badge.interrupted { background:#fff5dd; color:#946407; }
.progress-bar-track { height:8px; margin:14px 0; overflow:hidden; border-radius:100px; background:#eef1f6; }
.progress-bar-fill { height:100%; border-radius:100px; background:#315fce; transition:width .3s ease; }
.progress-bar-fill.failed { background:#c14242; }
.progress-bar-fill.interrupted { background:#c99a25; }
.progress-bar-fill.completed { background:#178454; }
.progress-stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); gap:10px; }
.progress-stats > div { display:flex; flex-direction:column; gap:4px; }
.progress-stats small, .info-list dt, .island-stats small { color:var(--muted); font-size:10px; }
.progress-stats strong { font-size:12px; overflow-wrap:anywhere; }
.progress-last { margin:12px 0 0; color:#57647a; font-size:11px; overflow-wrap:anywhere; }
.table-scroll { max-height:340px; overflow:auto; }
.iteration-table { width:100%; border-collapse:collapse; font-size:11px; }
.iteration-table th { position:sticky; top:0; padding:7px 8px; border-bottom:1px solid var(--line); background:#f8f9fc; color:#78849a; font-size:10px; text-align:left; }
.iteration-table td { padding:7px 8px; border-bottom:1px solid #f0f2f6; vertical-align:middle; }
.iteration-table tbody tr:hover { background:#fafbfe; }
.iteration-table a { color:var(--accent); text-decoration:none; }
.iteration-table a:hover { text-decoration:underline; }
.metrics-cell { max-width:230px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.iteration-state { padding:3px 7px; border-radius:100px; background:#eef1f5; color:#68748a; font-size:9px; font-weight:700; }
.iteration-state.completed { background:#e7f7ef; color:#178454; }
.iteration-state.failed { background:#fff0f0; color:#c14242; }
.island-card { padding:12px; border:1px solid var(--line); border-radius:10px; background:#fafbfe; }
.island-card header { display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
.island-card header strong { font-size:12px; }
.island-card header span { color:var(--muted); font-size:10px; }
.island-stats { display:grid; grid-template-columns:repeat(2,1fr); gap:8px; margin-top:10px; }
.island-stats > div { display:flex; flex-direction:column; gap:2px; }
.island-stats b { font-size:12px; }
.island-best { margin:10px 0 0; color:var(--muted); font-size:10px; overflow-wrap:anywhere; }
.island-best code { font-size:10px; }
.detail-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; }
.info-list { margin:0; }
.info-list > div { display:flex; flex-direction:column; gap:4px; padding:7px 0; border-bottom:1px solid #f0f2f6; }
.info-list > div:last-child { border-bottom:0; }
.info-list dt { font-weight:600; }
.info-list dd { margin:0; font-size:12px; overflow-wrap:anywhere; }
.checkpoint-list, .file-list { margin:0; padding:0; list-style:none; }
.checkpoint-list li { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:8px 0; border-bottom:1px solid #f0f2f6; }
.checkpoint-list li:last-child { border-bottom:0; }
.checkpoint-list strong { font-size:11px; }
.checkpoint-list small { display:block; margin-top:3px; color:var(--muted); font-size:10px; }
.file-list li { display:flex; align-items:center; justify-content:space-between; padding:7px 0; border-bottom:1px solid #f0f2f6; font-size:11px; }
.file-list li:last-child { border-bottom:0; }
.file-state { padding:3px 7px; border-radius:100px; font-size:10px; font-weight:700; }
.file-state.ok { background:#e7f7ef; color:#178454; }
.file-state.missing { background:#fff0f0; color:#c14242; }
.log-card { margin-top:16px; }
.log-size { margin-left:8px; color:var(--muted); font-weight:400; font-size:11px; }
.log-scroll { margin-left:12px; color:var(--muted); font-weight:400; font-size:11px; }
.log-scroll input { accent-color:var(--accent); }
.log-view { max-height:52vh; margin:0; overflow:auto; padding:12px; border-radius:8px; background:#172033; color:#d7e0f1; font:10px/1.5 "IBM Plex Mono",monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
.empty-hint { margin:0; color:var(--muted); font-size:11px; }
</style>
