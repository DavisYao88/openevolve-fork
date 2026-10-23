<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";

const route = useRoute();
const router = useRouter();
const data = ref({ archive: [], nodes: [], edges: [], checkpoint_dir: "" });
const selectedId = ref("");
const metric = ref(localStorage.getItem("openevolve.metric") || "");
const highlight = ref("top");
const view = ref("tree");
const showIslands = ref(false);
const search = ref("");
const sort = ref("generation");
const loading = ref(true);
const error = ref("");
const progress = ref(null);
const progressError = ref("");
let refreshTimer;
let progressTimer;

const nodes = computed(() => data.value.nodes || []);
const metrics = computed(() => [...new Set(nodes.value.flatMap((node) => Object.keys(node.metrics || {})))]);
const activeMetric = computed(() => metrics.value.includes(metric.value) ? metric.value : metrics.value[0] || "combined_score");
const selected = computed(() => nodes.value.find((node) => node.id === selectedId.value));
const scoreValues = computed(() => nodes.value
  .map((node) => node.metrics?.[activeMetric.value])
  .filter((score) => typeof score === "number" && Number.isFinite(score)));
const bestScore = computed(() => scoreValues.value.length ? Math.max(...scoreValues.value) : null);
const averageScore = computed(() => scoreValues.value.length ? scoreValues.value.reduce((sum, score) => sum + score, 0) / scoreValues.value.length : null);
const maxGeneration = computed(() => Math.max(0, ...nodes.value.map((node) => Number(node.generation) || 0)));
const islandIds = computed(() => [...new Set(nodes.value.map((node) => node.island))].sort((a, b) => a - b));
const chartWidth = 1120;
const chartHeight = computed(() => Math.max(480, nodes.value.length * 54 + 90));

const runId = computed(() => route.query.run_id || "");
const statusLabels = { running: "运行中", completed: "已完成", failed: "失败", interrupted: "已中断", pending: "等待启动" };
const stageLabels = { llm: "模型生成", diff: "差异应用", evaluation: "评测" };
const progressPercent = computed(() => {
  if (!progress.value) return 0;
  if (Number.isFinite(progress.value.percent)) return progress.value.percent;
  const total = progress.value.total_iterations || 0;
  return total ? Math.min(100, (progress.value.finished_iterations / total) * 100) : 0;
});
const lastIteration = computed(() => {
  const iterations = progress.value?.iterations || [];
  return iterations.length ? iterations[iterations.length - 1] : null;
});

const coordinates = computed(() => {
  const grouped = new Map();
  const ordered = [...nodes.value].sort((a, b) => {
    const generationDiff = (a.generation || 0) - (b.generation || 0);
    return generationDiff || (a.island || 0) - (b.island || 0) || String(a.id).localeCompare(String(b.id));
  });
  const positions = new Map();
  const rowHeight = Math.max(44, Math.min(78, 600 / Math.max(ordered.length, 1)));
  ordered.forEach((node) => {
    const groupKey = showIslands.value ? `${node.island}:${node.generation}` : String(node.generation);
    const row = grouped.get(groupKey) || 0;
    grouped.set(groupKey, row + 1);
    const totalRows = Math.max(1, ordered.filter((item) => {
      const key = showIslands.value ? `${item.island}:${item.generation}` : String(item.generation);
      return key === groupKey;
    }).length);
    const bucketStart = [...grouped.keys()].indexOf(groupKey);
    const y = 46 + (bucketStart * 1.25 + row / totalRows) * Math.max(rowHeight, 54);
    const x = 70 + (Number(node.generation) || 0) / Math.max(maxGeneration.value, 1) * (chartWidth - 150);
    positions.set(node.id, { x, y });
  });
  return positions;
});

const visibleNodes = computed(() => {
  const query = search.value.trim().toLowerCase();
  const result = nodes.value.filter((node) => !query || String(node.id).toLowerCase().includes(query));
  const sorted = [...result];
  if (sort.value === "score") {
    sorted.sort((a, b) => {
      const aScore = Number(a.metrics?.[activeMetric.value]);
      const bScore = Number(b.metrics?.[activeMetric.value]);
      return (Number.isFinite(bScore) ? bScore : -Infinity) - (Number.isFinite(aScore) ? aScore : -Infinity);
    });
  } else if (sort.value === "island") {
    sorted.sort((a, b) => (a.island || 0) - (b.island || 0));
  } else if (sort.value === "id") {
    sorted.sort((a, b) => String(a.id).localeCompare(String(b.id)));
  } else {
    sorted.sort((a, b) => (a.generation || 0) - (b.generation || 0));
  }
  return sorted;
});

function isHighlighted(node) {
  if (highlight.value === "top") return bestScore.value !== null && Number(node.metrics?.[activeMetric.value]) === bestScore.value;
  if (highlight.value === "first") return Number(node.generation) === 0;
  if (highlight.value === "failed") return node.metrics?.error != null;
  if (highlight.value === "unset") return node.metrics?.[activeMetric.value] == null;
  if (highlight.value === "archive") return (data.value.archive || []).includes(node.id);
  return false;
}

function scoreLabel(node) {
  const score = node.metrics?.[activeMetric.value];
  return Number.isFinite(score) ? score.toFixed(3) : "N/A";
}

async function loadData() {
  try {
    error.value = "";
    const response = await api.evolution(route.query.run_id || "");
    data.value = response;
    if (!metric.value || !metrics.value.includes(metric.value)) metric.value = metrics.value[0] || "";
    if (!selected.value && nodes.value.length) selectedId.value = "";
  } catch (cause) {
    error.value = cause.message;
  } finally {
    loading.value = false;
  }
}

async function loadProgress() {
  if (!runId.value) {
    progress.value = null;
    return;
  }
  try {
    progressError.value = "";
    progress.value = await api.runProgress(runId.value);
  } catch (cause) {
    progressError.value = cause.message;
  }
}

function selectNode(id) {
  selectedId.value = id;
}

function openProgram(node) {
  const run = route.query.run_id ? `?run_id=${encodeURIComponent(route.query.run_id)}` : "";
  const location = router.resolve(`/program/${encodeURIComponent(node.id)}${run}`);
  window.open(location.href, "_blank", "noopener");
}

function parentOf(node) {
  return nodes.value.find((item) => item.id === node.parent_id);
}

function childNodes(node) {
  return nodes.value.filter((item) => item.parent_id === node.id);
}

function metricY(node) {
  const value = node.metrics?.[activeMetric.value];
  const min = Math.min(...scoreValues.value);
  const max = Math.max(...scoreValues.value);
  if (!Number.isFinite(value)) return 420;
  if (min === max) return 220;
  return 390 - ((value - min) / (max - min)) * 340;
}

function metricX(node) {
  return 80 + ((Number(node.generation) || 0) / Math.max(maxGeneration.value, 1)) * 960;
}

function saveMetric() {
  localStorage.setItem("openevolve.metric", metric.value);
}

onMounted(() => {
  loadData();
  loadProgress();
  refreshTimer = window.setInterval(loadData, 3000);
  progressTimer = window.setInterval(loadProgress, 3000);
});
onUnmounted(() => {
  window.clearInterval(refreshTimer);
  window.clearInterval(progressTimer);
});
</script>

<template>
  <section>
    <div class="page-heading">
      <div>
        <p class="eyebrow">Evolution Monitor</p>
        <h1>进化可视化</h1>
        <p>{{ route.query.run_id ? `任务 ${route.query.run_id}` : (data.checkpoint_dir || "等待检查点数据") }}</p>
      </div>
      <div class="view-switch">
        <button v-for="item in [['tree','进化树'],['trend','性能趋势'],['list','程序列表']]" :key="item[0]" class="button" :class="{ selected: view === item[0] }" @click="view = item[0]">{{ item[1] }}</button>
      </div>
    </div>

    <div class="metric-controls card">
      <label>评分指标
        <select v-model="metric" class="select-input" @change="saveMetric">
          <option v-for="item in metrics" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label>高亮规则
        <select v-model="highlight" class="select-input">
          <option value="none">不高亮</option>
          <option value="top">最高分</option>
          <option value="first">初始代</option>
          <option value="failed">评测失败</option>
          <option value="unset">指标缺失</option>
          <option value="archive">精英档案</option>
        </select>
      </label>
      <label v-if="view !== 'list'" class="check-control"><input v-model="showIslands" type="checkbox">按岛屿分组</label>
      <span class="refresh-label"><span class="live-dot"></span>每 3 秒刷新</span>
    </div>

    <p v-if="progressError" class="message error">{{ progressError }}</p>
    <section v-if="progress" class="card run-progress">
      <div class="progress-head">
        <div>
          <p class="eyebrow">Run Progress</p>
          <h2>{{ runId }}</h2>
        </div>
        <span class="status-badge" :class="progress.status">{{ statusLabels[progress.status] || progress.status }}</span>
      </div>
      <div class="progress-bar-track" role="progressbar" :aria-valuenow="progressPercent" aria-valuemin="0" aria-valuemax="100">
        <div class="progress-bar-fill" :class="progress.status" :style="{ width: `${progressPercent}%` }"></div>
      </div>
      <div class="progress-stats">
        <div><small>进度</small><strong>{{ progress.finished_iterations }} / {{ progress.total_iterations }}</strong></div>
        <div><small>成功</small><strong>{{ progress.completed_iterations }}</strong></div>
        <div><small>失败</small><strong>{{ progress.failed_iterations }}</strong></div>
        <div><small>检查点</small><strong>{{ progress.has_checkpoint ? progress.checkpoints.join(", ") : "尚未生成" }}</strong></div>
        <div><small>token</small><strong>{{ progress.tokens?.prompt || 0 }} / {{ progress.tokens?.completion || 0 }}</strong></div>
      </div>
      <p v-if="lastIteration" class="progress-last">
        最近一轮：第 {{ lastIteration.iteration }} 轮 ·
        <b>{{ lastIteration.status === "completed" ? "成功" : "失败" }}</b>
        <template v-if="lastIteration.stage"> · 阶段 {{ stageLabels[lastIteration.stage] || lastIteration.stage }}</template>
        <template v-if="lastIteration.error"> · {{ lastIteration.error }}</template>
      </p>
      <p v-if="progress.status === 'running' && !progress.has_checkpoint" class="progress-hint">
        正在运行。检查点按固定轮次间隔保存，生成之前下方图表不会出现程序节点。
      </p>
    </section>

    <p v-if="error" class="message error">{{ error }}</p>
    <div v-else-if="loading" class="card empty-state">正在读取进化数据…</div>
    <div v-else-if="!nodes.length" class="card empty-state">当前没有可视化数据。启动任务后，检查点会自动显示在这里。</div>

    <div v-else-if="view === 'tree'" class="workspace">
      <section class="card chart-card">
        <div class="chart-heading">
          <div><h2>程序演化关系</h2><p>横轴为代数；节点大小代表当前指标表现</p></div>
          <span class="count-pill">{{ nodes.length }} 个程序 · {{ maxGeneration + 1 }} 代</span>
        </div>
        <div class="svg-scroll">
          <svg :viewBox="`0 0 ${chartWidth} ${Math.max(chartHeight, 480)}`" class="tree-chart" role="img" aria-label="程序演化关系图">
            <g v-for="edge in data.edges" :key="`${edge.source}-${edge.target}`">
              <line v-if="coordinates.has(edge.source) && coordinates.has(edge.target)" :x1="coordinates.get(edge.source).x" :y1="coordinates.get(edge.source).y" :x2="coordinates.get(edge.target).x" :y2="coordinates.get(edge.target).y" class="tree-edge" />
            </g>
            <g v-for="generation in maxGeneration + 1" :key="generation">
              <line :x1="70 + (generation - 1) / Math.max(maxGeneration, 1) * (chartWidth - 150)" y1="28" :x2="70 + (generation - 1) / Math.max(maxGeneration, 1) * (chartWidth - 150)" :y2="Math.max(chartHeight, 480) - 20" class="generation-guide" />
              <text :x="70 + (generation - 1) / Math.max(maxGeneration, 1) * (chartWidth - 150)" y="19" text-anchor="middle" class="axis-label">第 {{ generation - 1 }} 代</text>
            </g>
            <g v-for="node in nodes" :key="node.id" class="tree-node" :class="{ chosen: selectedId === node.id, highlighted: isHighlighted(node) }" @click="selectNode(node.id)">
              <circle :cx="coordinates.get(node.id)?.x" :cy="coordinates.get(node.id)?.y" :r="isHighlighted(node) ? 14 : 10" :fill="isHighlighted(node) ? '#e8a63b' : `hsl(${(Number(node.island || 0) * 67 + 210) % 360} 58% 55%)`" />
              <text :x="coordinates.get(node.id)?.x + 16" :y="coordinates.get(node.id)?.y + 4" class="node-label">{{ scoreLabel(node) }}</text>
            </g>
          </svg>
        </div>
      </section>
      <aside v-if="selected" class="card detail-card">
        <NodeDetail :node="selected" :nodes="nodes" :archive="data.archive || []" :metric="activeMetric" @close="selectedId = ''" @open="openProgram" />
      </aside>
    </div>

    <section v-else-if="view === 'trend'" class="card chart-card">
      <div class="chart-heading">
        <div><h2>指标趋势</h2><p>{{ activeMetric }} · 按代数查看分数变化</p></div>
        <span class="count-pill">最高 {{ bestScore === null ? "—" : bestScore.toFixed(4) }}</span>
      </div>
      <div class="svg-scroll">
        <svg viewBox="0 0 1120 460" class="trend-chart" role="img" aria-label="指标趋势散点图">
          <line x1="70" y1="390" x2="1070" y2="390" class="chart-axis" />
          <line x1="70" y1="40" x2="70" y2="390" class="chart-axis" />
          <text x="570" y="440" text-anchor="middle" class="axis-label">进化代数</text>
          <g v-for="node in nodes" :key="node.id" class="trend-point" :class="{ highlighted: isHighlighted(node), chosen: selectedId === node.id }" @click="selectNode(node.id)">
            <line v-if="parentOf(node)" :x1="metricX(parentOf(node))" :y1="metricY(parentOf(node))" :x2="metricX(node)" :y2="metricY(node)" class="trend-edge" />
            <circle :cx="metricX(node)" :cy="metricY(node)" r="7" :fill="`hsl(${(Number(node.island || 0) * 67 + 210) % 360} 58% 55%)`" />
          </g>
        </svg>
      </div>
      <p v-if="selected" class="selected-caption">已选择 {{ selected.id }} · {{ activeMetric }}: {{ scoreLabel(selected) }}</p>
    </section>

    <section v-else class="card list-card">
      <div class="list-toolbar">
        <input v-model="search" class="text-input" placeholder="搜索程序 ID" aria-label="搜索程序 ID">
        <select v-model="sort" class="select-input" aria-label="排序方式">
          <option value="generation">按代数</option><option value="score">按指标</option><option value="island">按岛屿</option><option value="id">按 ID</option>
        </select>
      </div>
      <div class="summary-row">
        <div><small>最高分</small><strong>{{ bestScore === null ? "—" : bestScore.toFixed(4) }}</strong></div>
        <div><small>平均分</small><strong>{{ averageScore === null ? "—" : averageScore.toFixed(4) }}</strong></div>
        <div><small>岛屿</small><strong>{{ islandIds.length }}</strong></div>
        <div><small>结果</small><strong>{{ visibleNodes.length }} / {{ nodes.length }}</strong></div>
      </div>
      <div class="program-list">
        <button v-for="node in visibleNodes" :key="node.id" class="program-row" :class="{ active: selectedId === node.id, highlighted: isHighlighted(node) }" @click="selectNode(node.id)">
          <span class="program-score">{{ scoreLabel(node) }}</span>
          <span class="program-meta"><strong class="mono">{{ node.id }}</strong><small>第 {{ node.generation ?? "?" }} 代 · 岛屿 {{ node.island ?? "?" }}</small></span>
          <span class="row-arrow">→</span>
        </button>
      </div>
      <NodeDetail v-if="selected" :node="selected" :nodes="nodes" :archive="data.archive || []" :metric="activeMetric" @close="selectedId = ''" @open="openProgram" />
    </section>
  </section>
</template>

<script>
const NodeDetail = {
  props: { node: Object, nodes: Array, archive: Array, metric: String },
  emits: ["close", "open"],
  data() { return { active: "概览" }; },
  computed: {
    parent() { return this.nodes.find((item) => item.id === this.node.parent_id); },
    children() { return this.nodes.filter((item) => item.parent_id === this.node.id); },
    cloneNodes() {
      const base = String(this.node.id).split("-copy")[0];
      return this.nodes.filter((item) => item.id !== this.node.id && String(item.id).split("-copy")[0] === base);
    },
    tabs() {
      return ["概览", ...(this.node.code ? ["代码"] : []), ...(this.node.prompts && Object.keys(this.node.prompts).length ? ["提示词"] : []), ...(this.children.length ? ["子程序"] : []), ...(this.cloneNodes.length ? ["克隆"] : []), ...(this.parent?.code && this.node.code ? ["差异"] : [])];
    },
    promptText() { return JSON.stringify(this.node.prompts || this.node.artifacts_json || {}, null, 2); },
    diffLines() {
      const parent = (this.parent?.code || "").split("\n");
      const current = (this.node.code || "").split("\n");
      const max = Math.max(parent.length, current.length);
      return Array.from({ length: max }, (_, index) => {
        if (parent[index] === current[index]) return { type: "same", text: current[index] || "" };
        return { type: parent[index] === undefined ? "added" : current[index] === undefined ? "removed" : "changed", text: current[index] ?? `− ${parent[index]}` };
      });
    },
  },
  template: `
    <div class="node-detail">
      <div class="detail-head"><div><p class="eyebrow">Program Details</p><h2>程序详情</h2></div><button class="icon-button" aria-label="关闭详情" @click="$emit('close')">×</button></div>
      <div class="node-stats">
        <div><small>程序 ID</small><strong class="mono">{{ node.id }}</strong></div>
        <div><small>代数 / 岛屿</small><strong>{{ node.generation ?? "—" }} / {{ node.island ?? "—" }}</strong></div>
        <div><small>{{ metric }}</small><strong>{{ typeof node.metrics?.[metric] === "number" && Number.isFinite(node.metrics[metric]) ? node.metrics[metric].toFixed(4) : "—" }}</strong></div>
        <div><small>父程序</small><strong class="mono">{{ node.parent_id || "无" }}</strong></div>
      </div>
      <div class="metric-tags"><span v-for="(value, name) in node.metrics || {}" :key="name"><small>{{ name }}</small><b>{{ value }}</b></span></div>
      <div class="detail-tabs"><button v-for="tab in tabs" :key="tab" :class="{ active: active === tab }" @click="active = tab">{{ tab }}</button></div>
      <div class="detail-body">
        <template v-if="active === '概览'">
          <p class="archive-label" v-if="archive.includes(node.id)">★ MAP-Elites 精英档案程序</p>
          <p>创建时间：{{ node.created_at || "未记录" }}</p>
          <p>子程序：{{ children.length }}　克隆：{{ cloneNodes.length }}</p>
          <button class="button" @click="$emit('open', node)">在新窗口打开完整程序页</button>
        </template>
        <pre v-else-if="active === '代码'" class="code-panel">{{ node.code }}</pre>
        <pre v-else-if="active === '提示词'" class="code-panel">{{ promptText }}</pre>
        <ul v-else-if="active === '子程序'"><li v-for="child in children" :key="child.id" class="mono">{{ child.id }} · 第 {{ child.generation }} 代</li></ul>
        <ul v-else-if="active === '克隆'"><li v-for="clone in cloneNodes" :key="clone.id" class="mono">{{ clone.id }}</li></ul>
        <div v-else class="diff-panel"><div v-for="(line, index) in diffLines" :key="index" :class="line.type">{{ line.text }}</div></div>
      </div>
    </div>
  `,
};

export default { components: { NodeDetail } };
</script>

<style scoped>
.run-progress { padding: 16px 18px; margin-bottom: 18px; }
.progress-head { display:flex; align-items:center; justify-content:space-between; gap:14px; }
.progress-head h2 { margin:2px 0 0; font:600 14px "IBM Plex Mono", monospace; }
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
.progress-stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(110px, 1fr)); gap:10px; }
.progress-stats > div { display:flex; flex-direction:column; gap:4px; }
.progress-stats small { color:var(--muted); font-size:10px; }
.progress-stats strong { font-size:12px; overflow-wrap:anywhere; }
.progress-last, .progress-hint { margin:12px 0 0; color:#57647a; font-size:11px; overflow-wrap:anywhere; }
.progress-hint { color:var(--muted); }
.view-switch { display:flex; gap:5px; padding:4px; border:1px solid var(--line); border-radius:10px; background:#fff; }
.view-switch .button { min-height:34px; border:0; background:transparent; box-shadow:none; }
.view-switch .button.selected { background:var(--accent-soft); color:var(--accent); }
.metric-controls { display:flex; align-items:center; flex-wrap:wrap; gap:14px 24px; padding:12px 16px; margin-bottom:18px; }
.metric-controls label { display:flex; align-items:center; gap:9px; color:var(--muted); font-size:12px; font-weight:600; }
.metric-controls .select-input { width:auto; min-width:160px; min-height:34px; padding:6px 10px; font-size:12px; }
.check-control input { accent-color:var(--accent); }
.refresh-label { display:flex; align-items:center; gap:7px; margin-left:auto; color:var(--muted); font-size:11px; }
.live-dot { width:7px; height:7px; border-radius:50%; background:#37a775; box-shadow:0 0 0 3px #37a77520; }
.workspace { display:grid; grid-template-columns:minmax(0,1fr) minmax(310px,360px); gap:16px; align-items:start; }
.chart-card { min-width:0; overflow:hidden; }
.chart-heading { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:20px 22px 14px; }
.chart-heading h2 { margin:0; font-size:15px; }
.chart-heading p { margin:5px 0 0; color:var(--muted); font-size:11px; }
.count-pill { padding:6px 10px; border-radius:100px; background:#f3f5fa; color:#65728a; font-size:11px; white-space:nowrap; }
.svg-scroll { overflow:auto; border-top:1px solid #f0f2f6; }
.tree-chart { display:block; width:100%; min-width:760px; }
.tree-edge { stroke:#c9d1df; stroke-width:1.5; }
.generation-guide { stroke:#e9edf3; stroke-dasharray:3 5; }
.axis-label { fill:#8792a5; font-size:11px; }
.tree-node { cursor:pointer; }
.tree-node circle { stroke:#fff; stroke-width:2.5; transition:r .15s, filter .15s; }
.tree-node:hover circle, .tree-node.chosen circle { r:15; stroke:#263b79; }
.tree-node.highlighted circle { stroke:#e59e2d; stroke-width:3; }
.node-label { fill:#68748a; font-size:10px; pointer-events:none; }
.detail-card { position:sticky; top:92px; overflow:hidden; }
.node-detail { padding:18px; }
.detail-head { display:flex; justify-content:space-between; align-items:center; }
.detail-head .eyebrow { margin-bottom:3px; }
.detail-head h2 { margin:0; font-size:17px; }
.icon-button { width:30px; height:30px; border:0; border-radius:8px; background:#f2f4f8; color:#68748a; font-size:20px; }
.node-stats { display:grid; gap:10px; margin-top:17px; }
.node-stats > div { display:flex; flex-direction:column; gap:4px; min-width:0; }
.node-stats small, .metric-tags small { color:var(--muted); font-size:10px; }
.node-stats strong { overflow-wrap:anywhere; font-size:12px; }
.metric-tags { display:flex; flex-wrap:wrap; gap:6px; margin-top:13px; }
.metric-tags span { display:flex; flex-direction:column; gap:3px; padding:6px 8px; border:1px solid var(--line); border-radius:7px; }
.metric-tags b { font-size:11px; }
.detail-tabs { display:flex; flex-wrap:wrap; gap:5px; margin:16px 0 0; padding-top:12px; border-top:1px solid var(--line); }
.detail-tabs button { padding:6px 8px; border:0; border-radius:6px; background:#f4f6fa; color:#68748a; font-size:10px; }
.detail-tabs button.active { background:var(--accent-soft); color:var(--accent); font-weight:700; }
.detail-body { max-height:55vh; overflow:auto; padding-top:11px; color:#57647a; font-size:12px; }
.detail-body p { margin:8px 0; }
.code-panel, .diff-panel { overflow:auto; max-height:48vh; margin:0; padding:12px; border-radius:8px; background:#172033; color:#d7e0f1; font:11px/1.55 "IBM Plex Mono",monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
.diff-panel div { padding:1px 5px; }
.diff-panel .added { background:#174d3b; color:#b4f0d4; }
.diff-panel .removed { background:#592d36; color:#ffd0d0; }
.diff-panel .changed { color:#ffe1a2; }
.archive-label { color:#b27810; font-weight:700; }
.trend-chart { display:block; width:100%; min-width:720px; height:min(65vh,650px); }
.chart-axis { stroke:#bdc7d6; }
.trend-edge { stroke:#cbd3e0; stroke-width:1; }
.trend-point { cursor:pointer; }
.trend-point circle { stroke:#fff; stroke-width:2; }
.trend-point.highlighted circle, .trend-point.chosen circle { stroke:#e5a02c; stroke-width:3; }
.selected-caption { padding:0 22px 14px; color:var(--muted); font-size:12px; }
.list-card { overflow:hidden; }
.list-toolbar { display:flex; gap:10px; padding:16px; border-bottom:1px solid var(--line); }
.list-toolbar .text-input { max-width:360px; }
.list-toolbar .select-input { width:auto; min-width:140px; }
.summary-row { display:grid; grid-template-columns:repeat(4,1fr); border-bottom:1px solid var(--line); }
.summary-row > div { display:flex; flex-direction:column; gap:5px; padding:14px 18px; border-right:1px solid var(--line); }
.summary-row > div:last-child { border:0; }
.summary-row small { color:var(--muted); font-size:10px; }
.summary-row strong { font-size:15px; }
.program-list { max-height:65vh; overflow:auto; }
.program-row { display:flex; width:100%; align-items:center; gap:14px; padding:12px 18px; border:0; border-bottom:1px solid #f0f2f6; background:#fff; text-align:left; }
.program-row:hover, .program-row.active { background:#f5f7fc; }
.program-row.highlighted .program-score { color:#a46a0b; }
.program-score { min-width:70px; color:#315fce; font:600 13px "IBM Plex Mono",monospace; }
.program-meta { display:flex; min-width:0; flex:1; flex-direction:column; gap:4px; }
.program-meta strong { overflow:hidden; text-overflow:ellipsis; font-size:11px; }
.program-meta small { color:var(--muted); font-size:10px; }
.row-arrow { color:#98a2b3; }
.list-card > .node-detail { border-top:1px solid var(--line); }
@media(max-width:1000px) { .workspace { grid-template-columns:1fr; } .detail-card { position:static; } }
@media(max-width:640px) {
  .page-heading { align-items:flex-start; flex-direction:column; }
  .metric-controls { gap:10px; }
  .refresh-label { margin-left:0; }
  .summary-row { grid-template-columns:repeat(2,1fr); }
  .summary-row > div:nth-child(2) { border-right:0; }
  .summary-row > div:nth-child(-n+2) { border-bottom:1px solid var(--line); }
}
</style>
