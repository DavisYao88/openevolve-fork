<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { api } from "../api";
import { highlightLine } from "../highlight";

const route = useRoute();
const router = useRouter();

const error = ref("");
const data = ref({ nodes: [], archive: [], checkpoints: [], checkpoint_dir: "" });
const checkpointList = ref([]);
const selectedId = ref("");
const fileTab = ref("current");
const inputCode = ref("");
const bestCode = ref("");
const collapsed = ref(new Set());

const runId = computed(() => route.query.run_id || "");
const checkpoint = computed(() => route.query.checkpoint || "");
const nodes = computed(() => data.value.nodes || []);
// 早期轮次的程序可能已经不在最新检查点里，需要跨检查点兜底查找
const fallback = ref({ node: null, parent: null });
const fallbackCheckpoint = ref("");

const node = computed(() => nodes.value.find((item) => item.id === selectedId.value) || fallback.value.node || null);
const parentNode = computed(() => {
  const parentId = node.value?.parent_id;
  if (!parentId) return null;
  return nodes.value.find((item) => item.id === parentId) || fallback.value.parent || null;
});
const archive = computed(() => data.value.archive || []);

const FILE_TABS = [
  ["current", "本程序"],
  ["parent", "父程序"],
  ["diff", "对比 diff"],
  ["input", "初始输入"],
  ["best", "最优输出"],
];

const islandGroups = computed(() => {
  const groups = new Map();
  for (const item of nodes.value) {
    const key = item.island ?? 0;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return [...groups.entries()]
    .sort((a, b) => Number(a[0]) - Number(b[0]))
    .map(([island, items]) => ({
      island,
      items: items.slice().sort((a, b) => scoreOf(b) - scoreOf(a)),
    }));
});

function scoreOf(item) {
  const value = Number(item?.metrics?.combined_score);
  return Number.isFinite(value) ? value : -Infinity;
}

function scoreLabel(item) {
  return Number.isFinite(scoreOf(item)) ? scoreOf(item).toFixed(3) : "N/A";
}

const activeCode = computed(() => {
  if (fileTab.value === "parent") return parentNode.value?.code || "（没有父程序，或父程序不在当前检查点）";
  if (fileTab.value === "input") return inputCode.value || "（未找到 input/init.py）";
  if (fileTab.value === "best") return bestCode.value || "（还没有产出 best_program.py）";
  return node.value?.code || "无代码内容";
});

const codeLines = computed(() => String(activeCode.value).split("\n"));

// 行级 LCS 对齐：父程序在左、本程序在右
function diffLines(oldLines, newLines) {
  const rows = [];
  const n = oldLines.length;
  const m = newLines.length;
  if (n * m > 4_000_000) {
    for (let index = 0; index < Math.max(n, m); index += 1) {
      rows.push({
        left: index < n ? { num: index + 1, text: oldLines[index], type: "same" } : null,
        right: index < m ? { num: index + 1, text: newLines[index], type: "same" } : null,
      });
    }
    return rows;
  }
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] = oldLines[i] === newLines[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (oldLines[i] === newLines[j]) {
      rows.push({
        left: { num: i + 1, text: oldLines[i], type: "same" },
        right: { num: j + 1, text: newLines[j], type: "same" },
      });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      rows.push({ left: { num: i + 1, text: oldLines[i], type: "del" }, right: null });
      i += 1;
    } else {
      rows.push({ left: null, right: { num: j + 1, text: newLines[j], type: "add" } });
      j += 1;
    }
  }
  while (i < n) {
    rows.push({ left: { num: i + 1, text: oldLines[i], type: "del" }, right: null });
    i += 1;
  }
  while (j < m) {
    rows.push({ left: null, right: { num: j + 1, text: newLines[j], type: "add" } });
    j += 1;
  }
  return rows;
}

const diffRows = computed(() => diffLines(
  String(parentNode.value?.code || "").split("\n"),
  String(node.value?.code || "").split("\n"),
));

const diffStats = computed(() => ({
  add: diffRows.value.filter((row) => row.right?.type === "add").length,
  del: diffRows.value.filter((row) => row.left?.type === "del").length,
}));

const diffOnly = ref(false);
const visibleDiffRows = computed(() => (diffOnly.value
  ? diffRows.value.filter((row) => row.left?.type === "del" || row.right?.type === "add")
  : diffRows.value));

async function copyCode() {
  try {
    await navigator.clipboard.writeText(activeCode.value);
  } catch {
    /* 剪贴板不可用时忽略 */
  }
}

function toggleGroup(key) {
  const next = new Set(collapsed.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  collapsed.value = next;
}

function switchCheckpoint(name) {
  const query = { ...route.query };
  if (name) query.checkpoint = name;
  else delete query.checkpoint;
  router.replace({ query });
}

function pickDefault() {
  const wanted = route.params.id;
  if (wanted && nodes.value.some((item) => item.id === wanted)) return wanted;
  const sorted = [...nodes.value].sort((a, b) => scoreOf(b) - scoreOf(a));
  return sorted.length ? sorted[0].id : "";
}

async function loadFallback() {
  fallback.value = { node: null, parent: null };
  fallbackCheckpoint.value = "";
  if (!runId.value || !selectedId.value) return;
  if (nodes.value.some((item) => item.id === selectedId.value)) return;
  try {
    const response = await api.runProgram(runId.value, selectedId.value);
    fallback.value = { node: response.node || null, parent: response.parent || null };
    fallbackCheckpoint.value = response.checkpoint || "";
  } catch {
    /* 找不到就保持当前检查点的展示 */
  }
}

async function loadAll() {
  error.value = "";
  try {
    const [evolution, checkpoints] = await Promise.all([
      api.evolution(runId.value, checkpoint.value),
      runId.value ? api.runCheckpoints(runId.value) : Promise.resolve({ checkpoints: [] }),
    ]);
    data.value = evolution;
    checkpointList.value = checkpoints.checkpoints || [];
    if (!selectedId.value || !nodes.value.some((item) => item.id === selectedId.value)) {
      selectedId.value = pickDefault();
    }
  } catch (cause) {
    error.value = cause.message;
  }
  if (!runId.value) return;
  try {
    inputCode.value = (await api.runInputFile(runId.value, "init.py")).content || "";
  } catch {
    inputCode.value = "";
  }
  try {
    bestCode.value = (await api.runOutputFile(runId.value, "best_program.py")).content || "";
  } catch {
    bestCode.value = "";
  }
}

onMounted(loadAll);
watch(() => route.query.checkpoint || "", loadAll);
watch([selectedId, nodes], loadFallback);

const summary = computed(() => {
  if (!node.value) return [];
  const entries = [
    ["代数", node.value.generation ?? "—"],
    ["岛屿", node.value.island ?? "—"],
    ["发现轮次", node.value.iteration_found ?? "—"],
    ["语言", node.value.language || "python"],
  ];
  for (const [key, value] of Object.entries(node.value.metrics || {})) entries.push([key, value]);
  return entries;
});
</script>

<template>
  <section>
    <div class="page-heading">
      <div>
        <p class="eyebrow">Program</p>
        <h1>程序代码</h1>
        <p class="mono">{{ node?.id || route.params.id }}</p>
      </div>
      <div class="heading-actions">
        <RouterLink class="button" :to="{ path: '/', query: route.query }">← 返回可视化</RouterLink>
        <RouterLink v-if="runId" class="button" :to="{ path: `/runs/${runId}` }">任务详情</RouterLink>
      </div>
    </div>

    <p v-if="error" class="message error">{{ error }}</p>
    <div v-else-if="!nodes.length" class="card empty-state">当前检查点没有程序数据。</div>

    <div v-else class="program-layout">
      <aside class="card tree-pane">
        <h2>目录</h2>
        <div class="tree-group">
          <button class="tree-leaf root" @click="router.push({ path: '/', query: route.query })">
            任务 {{ runId || "（未指定）" }}
          </button>
        </div>

        <div class="tree-group">
          <span class="tree-group-title">检查点</span>
          <button class="tree-leaf" :class="{ active: !checkpoint }" @click="switchCheckpoint('')">最新</button>
          <button
            v-for="item in checkpointList"
            :key="item.name"
            class="tree-leaf"
            :class="{ active: checkpoint === item.name }"
            @click="switchCheckpoint(item.name)"
          >{{ item.name }} · {{ item.programs }} 程序</button>
        </div>

        <div v-for="group in islandGroups" :key="group.island" class="tree-group">
          <button class="tree-group-title tree-toggle" @click="toggleGroup(`island-${group.island}`)">
            {{ collapsed.has(`island-${group.island}`) ? "▸" : "▾" }} 岛屿 {{ group.island }}（{{ group.items.length }}）
          </button>
          <template v-if="!collapsed.has(`island-${group.island}`)">
            <button
              v-for="item in group.items"
              :key="item.id"
              class="tree-leaf program-leaf"
              :class="{ active: item.id === selectedId, elite: archive.includes(item.id) }"
              @click="selectedId = item.id"
            >
              <span class="mono">{{ String(item.id).slice(0, 8) }}</span>
              <span class="leaf-score">{{ scoreLabel(item) }}</span>
            </button>
          </template>
        </div>
      </aside>

      <section class="card editor-pane">
        <header class="editor-head">
          <div class="summary-row">
            <span v-for="[key, value] in summary" :key="key"><small>{{ key }}</small><b>{{ value }}</b></span>
            <span v-if="fallbackCheckpoint" class="source-tag"><small>来源检查点</small><b class="mono">{{ fallbackCheckpoint }}</b></span>
          </div>
          <button class="button" @click="copyCode">复制代码</button>
        </header>

        <div class="file-tabs">
          <button
            v-for="[key, label] in FILE_TABS"
            :key="key"
            class="button"
            :class="{ selected: fileTab === key }"
            @click="fileTab = key"
          >{{ label }}</button>
          <span class="file-hint">{{ fileTab === "input" ? "input/init.py" : fileTab === "best" ? "output/best_program.py" : fileTab === "diff" ? "左=父程序，右=本程序" : node?.id }}</span>
        </div>

        <div v-if="fileTab === 'diff'" class="diff-view">
          <div class="diff-head">
            <span>父程序 <code>{{ parentNode?.id ? String(parentNode.id).slice(0, 8) : "—" }}</code></span>
            <span>本程序 <code>{{ String(node?.id || "").slice(0, 8) }}</code></span>
            <label class="diff-only"><input v-model="diffOnly" type="checkbox">只看差异</label>
            <span class="diff-stat">+{{ diffStats.add }} / -{{ diffStats.del }}</span>
          </div>
          <p v-if="!parentNode" class="empty-hint">该程序没有父程序（属于初始代），无可对比内容。</p>
          <div v-else class="diff-body">
            <div v-for="(row, index) in visibleDiffRows" :key="index" class="diff-row">
              <div class="diff-side" :class="row.left ? row.left.type : 'empty'">
                <span class="diff-num">{{ row.left?.num ?? "" }}</span>
                <span class="diff-code" v-html="row.left ? highlightLine(row.left.text) : ''"></span>
              </div>
              <div class="diff-side" :class="row.right ? row.right.type : 'empty'">
                <span class="diff-num">{{ row.right?.num ?? "" }}</span>
                <span class="diff-code" v-html="row.right ? highlightLine(row.right.text) : ''"></span>
              </div>
            </div>
          </div>
        </div>

        <div v-else class="code-view">
          <div class="line-numbers">
            <span v-for="(_, index) in codeLines" :key="index">{{ index + 1 }}</span>
          </div>
          <pre class="code-body"><code><span v-for="(line, index) in codeLines" :key="index" class="code-line" v-html="highlightLine(line)"></span></code></pre>
        </div>

        <section v-if="node?.changes_description" class="extra-block">
          <h3>变更说明</h3>
          <p>{{ node.changes_description }}</p>
        </section>
        <section v-if="node?.artifact_dir" class="extra-block">
          <h3>产物目录</h3>
          <p class="mono">{{ node.artifact_dir }}</p>
        </section>
      </section>
    </div>
  </section>
</template>

<style scoped>
.heading-actions { display:flex; gap:8px; }
.program-layout { display:grid; grid-template-columns:260px minmax(0,1fr); gap:14px; align-items:start; }
.tree-pane { padding:14px; max-height:calc(100vh - 160px); overflow:auto; }
.tree-pane h2, .editor-pane h3 { margin:0 0 10px; font-size:13px; }
.tree-group { margin-bottom:12px; }
.tree-group-title { display:block; margin-bottom:6px; color:var(--muted); font-size:10px; font-weight:700; }
.tree-toggle { width:100%; padding:2px 0; border:0; background:none; text-align:left; cursor:pointer; }
.tree-leaf { display:flex; align-items:center; justify-content:space-between; gap:8px; width:100%; padding:5px 8px; margin-bottom:2px; border:0; border-radius:7px; background:none; color:#3a4658; font-size:11px; text-align:left; cursor:pointer; }
.tree-leaf:hover { background:#f2f5ff; }
.tree-leaf.active { background:#eef3ff; color:var(--accent); font-weight:700; }
.tree-leaf.root { color:#315fce; font-weight:700; }
.leaf-score { color:#178454; font-family:"IBM Plex Mono",monospace; font-size:10px; }
.program-leaf.elite { box-shadow:inset 2px 0 0 #e8a63b; }
.editor-pane { padding:16px 18px; }
.editor-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:12px; }
.summary-row { display:flex; flex-wrap:wrap; gap:8px; }
.summary-row span { display:flex; flex-direction:column; gap:2px; padding:6px 9px; border:1px solid var(--line); border-radius:8px; }
.summary-row small { color:var(--muted); font-size:10px; }
.summary-row b { font-size:11px; }
.file-tabs { display:flex; align-items:center; gap:6px; margin-bottom:10px; }
.file-tabs .button { min-height:26px; padding:3px 10px; font-size:11px; }
.file-tabs .button.selected { border-color:var(--accent); background:#eef3ff; color:var(--accent); }
.file-hint { margin-left:6px; color:var(--muted); font-size:10px; font-family:"IBM Plex Mono",monospace; }
.code-view { display:flex; min-height:460px; max-height:64vh; overflow:auto; border-radius:10px; background:#172033; }
.source-tag { border-color:#e8c98b !important; background:#fffaf0; }
.source-tag b { color:#946407; }
.line-numbers { flex:0 0 46px; padding:12px 8px; border-right:1px solid #26324a; color:#5a6b8a; font:11px/1.7 "IBM Plex Mono",monospace; text-align:right; user-select:none; }
.line-numbers span { display:block; }
.code-body { flex:1; margin:0; padding:12px 14px; color:#d7e0f1; font:11px/1.7 "IBM Plex Mono",monospace; white-space:pre; }
.code-line { display:block; }
.tok-comment { color:#7f8ea8; font-style:italic; }
.tok-string { color:#8fd6a0; }
.tok-keyword { color:#7fa9ff; font-weight:700; }
.tok-number { color:#e8b46b; }
.diff-view { border-radius:10px; overflow:hidden; border:1px solid #26324a; }
.diff-head { display:flex; align-items:center; gap:16px; padding:8px 12px; background:#1b2438; color:#9fb0cc; font-size:11px; }
.diff-head code { color:#d7e0f1; font-family:"IBM Plex Mono",monospace; }
.diff-only { display:flex; align-items:center; gap:5px; margin-left:auto; color:#9fb0cc; font-size:11px; cursor:pointer; }
.diff-only input { margin:0; }
.diff-stat { margin-left:14px; color:#8fd6a0; font-weight:700; }
.diff-body { max-height:60vh; overflow:auto; background:#172033; }
.diff-row { display:grid; grid-template-columns:1fr 1fr; }
.diff-side { display:flex; gap:8px; padding:0 10px; color:#d7e0f1; font:11px/1.7 "IBM Plex Mono",monospace; white-space:pre; }
.diff-side.empty { background:#141b2c; }
.diff-side.add { background:#16322a; }
.diff-side.del { background:#331d24; }
.diff-num { flex:0 0 34px; color:#5a6b8a; text-align:right; user-select:none; }
.diff-code { flex:1; overflow:hidden; text-overflow:ellipsis; }
.empty-hint { margin:0; padding:12px; color:#9fb0cc; font-size:11px; }
.extra-block { margin-top:14px; padding-top:12px; border-top:1px solid var(--line); }
.extra-block h3 { margin-bottom:6px; }
.extra-block p { margin:0; color:#57647a; font-size:11px; line-height:1.6; }
@media (max-width: 900px) {
  .program-layout { grid-template-columns:1fr; }
}
</style>
