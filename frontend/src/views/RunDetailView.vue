<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { api } from "../api";
import CodePreview from "../components/CodePreview.vue";

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
const report = ref(null);
const reportMissing = ref(false);
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
    const message = String(cause.message || cause);
    const hint = message.includes("404")
      ? "预览接口不存在：正在运行的后端是旧版本，请重启 scripts/visualizer.py 后再试。"
      : message;
    preview.value = { name, content: "", loading: false, truncated: false, size: 0, error: hint };
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

function onKeydown(event) {
  if (event.key === "Escape") closePreview();
}

async function loadReport() {
  if (!runId.value) return;
  try {
    report.value = await api.runReport(runId.value);
    reportMissing.value = false;
  } catch {
    report.value = null;
    reportMissing.value = true;
  }
}

function openReport(format) {
  window.open(api.runReportUrl(runId.value, format), "_blank", "noopener");
}

function fmtScore(value) {
  return Number.isFinite(value) ? Number(value).toFixed(4) : "—";
}

// 任务跑完后自动补拉报告（后端在演化结束时生成）
watch(
  () => detail.value?.status,
  (status) => {
    if (status && status !== "running" && !report.value) loadReport();
  }
);

onMounted(() => {
  loadDetail();
  loadCheckpoints();
  loadLog();
  loadReport();
  detailTimer = window.setInterval(loadDetail, 5000);
  logTimer = window.setInterval(() => { if (isRunning.value || !logEof.value) loadLog(); }, 3000);
  window.addEventListener("keydown", onKeydown);
});
onUnmounted(() => {
  window.clearInterval(detailTimer);
  window.clearInterval(logTimer);
  window.removeEventListener("keydown", onKeydown);
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
        <button v-if="isRunning" class="button" :disabled="acting" @click="cancelRun">■ 停止运行</button>
        <button class="button" :disabled="acting" @click="exportRun">⤓ 静态导出</button>
        <a v-if="report" class="button" :href="api.runReportUrl(runId, 'html')" target="_blank" rel="noopener">查看报告</a>
        <RouterLink class="button" :to="{ path: '/code', query: { run_id: runId } }">查看代码</RouterLink>
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
            <div><dt>任务目录</dt><dd class="mono">{{ detail.run_dir || "—" }}</dd></div>
            <div><dt>输入目录</dt><dd class="mono">{{ detail.run_dir ? `${detail.run_dir}/input` : "—" }}</dd></div>
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
          <h2 class="card-title">输入文件 <span class="card-hint">点击文件名预览内容</span></h2>
          <ul class="file-list">
            <li v-for="(exists, name) in inputFiles" :key="name">
              <button class="file-link mono" :disabled="!exists" :title="exists ? '预览文件内容' : '文件缺失'" @click="previewFile(name)">{{ name }}</button>
              <span class="file-state" :class="exists ? 'ok' : 'missing'">{{ exists ? "已保存" : "缺失" }}</span>
            </li>
          </ul>
        </section>
      </div>
      <section class="card report-card">
        <h2 class="card-title">运行报告
          <span v-if="report" class="log-size">演化结束时自动生成</span>
        </h2>
        <template v-if="report">
          <div class="report-stats">
            <div><small>最佳分数</small><strong>{{ fmtScore(report.best_score) }}</strong></div>
            <div><small>相对提升</small><strong>{{ report.improvement_percent != null ? `${report.improvement_percent.toFixed(2)}%` : "—" }}</strong></div>
            <div><small>主指标</small><strong class="mono">{{ report.primary_metric || "—" }}</strong></div>
            <div><small>生成时间</small><strong>{{ (report.generated_at || "—").replace("T", " ") }}</strong></div>
          </div>
          <div class="report-actions">
            <button class="button" @click="openReport('html')">新窗口查看完整报告</button>
            <button class="button" @click="openReport('md')">下载 Markdown</button>
          </div>
        </template>
        <p v-else-if="reportMissing" class="empty-hint">
          {{ isRunning ? "任务结束后会自动生成报告。" : "该任务没有报告（报告功能上线前的历史任务不补生成）。" }}
        </p>
        <p v-else class="empty-hint">正在读取报告…</p>
      </section>

      <section class="card log-card">
        <h2 class="card-title">运行日志
          <span class="log-size">{{ (logSize / 1024).toFixed(1) }} KB</span>
          <label class="log-scroll"><input v-model="autoScroll" type="checkbox">自动滚动</label>
        </h2>
        <pre ref="logPanel" class="log-view">{{ logText || "暂无日志" }}</pre>
      </section>
    </template>

    <div v-if="preview" class="modal-backdrop" @click.self="closePreview">
      <div class="modal-card" role="dialog" aria-modal="true" :aria-label="`${preview.name} 内容预览`">
        <header class="modal-head">
          <h3 class="mono">{{ preview.name }}</h3>
          <div class="modal-head-right">
            <span v-if="!preview.loading && !preview.error" class="modal-meta">{{ (preview.size / 1024).toFixed(1) }} KB<template v-if="preview.truncated"> · 已截断</template></span>
            <button class="button modal-close" @click="closePreview">✕ 关闭</button>
          </div>
        </header>
        <p v-if="preview.loading" class="modal-hint">正在读取…</p>
        <p v-else-if="preview.error" class="modal-hint error">{{ preview.error }}</p>
        <CodePreview
          v-else
          :content="preview.content"
          :filename="preview.name"
          copy-label="复制内容"
          max-height="none"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.detail-actions { display:flex; flex-wrap:wrap; gap:8px; }
.run-progress { margin-bottom:16px; }
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
.detail-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; align-items:start; }
.info-list { margin:0; }
.info-list > div { display:grid; grid-template-columns:88px 1fr; gap:10px; align-items:baseline; padding:8px 0; border-bottom:1px solid #f0f2f6; }
.info-list > div:last-child { border-bottom:0; }
.info-list dt { color:#78849a; font-size:11px; font-weight:600; }
.info-list dd { margin:0; font-size:12px; overflow-wrap:anywhere; }
.card-hint { margin-left:8px; color:#9aa4b6; font-weight:400; font-size:10px; }
.checkpoint-list { margin:0; padding:0; list-style:none; max-height:320px; overflow:auto; }
.file-list { margin:0; padding:0; list-style:none; }
.checkpoint-list li { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:8px 0; border-bottom:1px solid #f0f2f6; }
.checkpoint-list li:last-child { border-bottom:0; }
.checkpoint-list strong { font-size:11px; }
.checkpoint-list small { display:block; margin-top:3px; color:var(--muted); font-size:10px; }
.file-list li { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:6px 0; border-bottom:1px solid #f0f2f6; font-size:11px; }
.file-list li:last-child { border-bottom:0; }
.file-link { padding:2px 5px; margin-left:-5px; border:0; border-radius:6px; background:none; color:#315fce; font:11px "IBM Plex Mono",monospace; cursor:pointer; text-align:left; }
.file-link:hover:not(:disabled) { background:#eef3ff; text-decoration:underline; }
.file-link:disabled { color:#9aa4b6; cursor:not-allowed; }
.file-state { padding:3px 7px; border-radius:100px; font-size:10px; font-weight:700; }
.file-state.ok { background:#e7f7ef; color:#178454; }
.file-state.missing { background:#fff0f0; color:#c14242; }
.modal-backdrop { position:fixed; inset:0; z-index:60; display:flex; align-items:center; justify-content:center; padding:24px; background:#10182866; }
.modal-card { display:flex; flex-direction:column; width:min(860px,100%); max-height:82vh; border-radius:12px; background:#fff; box-shadow:0 18px 50px #10182833; overflow:hidden; }
.modal-head { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 16px; border-bottom:1px solid var(--line); }
.modal-head h3 { margin:0; font-size:13px; }
.modal-head-right { display:flex; align-items:center; gap:10px; }
.modal-meta { color:#78849a; font-size:11px; }
.modal-close { min-height:30px; padding:4px 10px; font-size:11px; }
.modal-hint { padding:20px 16px; color:#78849a; font-size:12px; }
.modal-hint.error { color:#c14242; }
/* 弹窗里的代码显示交给 components/CodePreview.vue（与抽屉、代码浏览页一致） */
.log-card { margin-top:16px; }
.report-card { margin-top:16px; }
.report-stats { display:flex; flex-wrap:wrap; gap:12px 32px; margin-bottom:14px; }
.report-stats > div { display:flex; flex-direction:column; gap:3px; min-width:0; }
.report-stats small { color:var(--muted); font-size:10px; }
.report-stats strong { font-size:12px; overflow-wrap:anywhere; }
.report-actions { display:flex; gap:8px; flex-wrap:wrap; }
.log-size { margin-left:8px; color:var(--muted); font-weight:400; font-size:11px; }
.log-scroll { margin-left:12px; color:var(--muted); font-weight:400; font-size:11px; }
.log-scroll input { accent-color:var(--accent); }
.log-view { max-height:52vh; margin:0; overflow:auto; padding:12px; border-radius:8px; background:#172033; color:#d7e0f1; font:10px/1.5 "IBM Plex Mono",monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
.empty-hint { margin:0; color:var(--muted); font-size:11px; }
</style>