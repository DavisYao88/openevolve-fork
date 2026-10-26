<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { api } from "../api";
import { highlightLine } from "../highlight";

const route = useRoute();
const router = useRouter();
const data = ref({ archive: [], nodes: [], edges: [], checkpoint_dir: "", checkpoints: [], feature_maps: [] });
const selectedId = ref("");
const metric = ref(localStorage.getItem("openevolve.metric") || "");
const highlight = ref("top");
const viewOptions = ["tree", "iterations", "trend", "list", "map"];
const view = ref(viewOptions.includes(route.query.view) ? route.query.view : "tree");
const selectedCheckpoint = ref("");
const progressOpen = ref(false);
const zoom = ref(1);
const panX = ref(0);
const panY = ref(0);
const showIslands = ref(true);
const search = ref("");
const sort = ref("score");
const loading = ref(true);
const error = ref("");
const progress = ref(null);
const progressError = ref("");
let refreshTimer;
let progressTimer;

const nodes = computed(() => data.value.nodes || []);
const metrics = computed(() => [...new Set(nodes.value.flatMap((node) => Object.keys(node.metrics || {})))]);
const activeMetric = computed(() => {
  if (metrics.value.includes(metric.value)) return metric.value;
  if (metrics.value.includes("combined_score")) return "combined_score";
  return metrics.value[0] || "combined_score";
});
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
const checkpoints = computed(() => data.value.checkpoints || []);
const featureMaps = computed(() => (data.value.feature_maps || []).filter((grid) => grid.cells?.length));
const mapFitnessRange = computed(() => {
  const values = featureMaps.value
    .flatMap((grid) => grid.cells.map((cell) => cell.fitness))
    .filter((value) => typeof value === "number" && Number.isFinite(value));
  if (!values.length) return { min: 0, max: 1 };
  return { min: Math.min(...values), max: Math.max(...values) };
});

function cellColor(cell) {
  if (typeof cell.fitness !== "number" || !Number.isFinite(cell.fitness)) return "#dfe5ee";
  const { min, max } = mapFitnessRange.value;
  const ratio = max === min ? 1 : (cell.fitness - min) / (max - min);
  return `hsl(${Math.round(210 - ratio * 170)} ${Math.round(45 + ratio * 40)}% ${Math.round(72 - ratio * 26)}%)`;
}

function gridGeometry(grid) {
  const size = 26;
  const binsX = Math.max(grid.bins_x || 1, 1);
  const binsY = Math.max(grid.bins_y || 1, 1);
  return { size, width: binsX * size, height: binsY * size };
}

function cellRect(cell, geometry, grid) {
  const binsY = Math.max(grid.bins_y || 1, 1);
  return { x: cell.x * geometry.size, y: (binsY - 1 - cell.y) * geometry.size };
}

const scrollBox = ref(null);

function resetView() {
  zoom.value = 1;
  panX.value = 0;
  panY.value = 0;
}

function fitView() {
  const box = scrollBox.value;
  if (!box) return;
  const { width, height } = activeTreeSize.value;
  const ratio = Math.min(box.clientWidth / width, box.clientHeight / height);
  zoom.value = Math.min(6, Math.max(0.3, Number.isFinite(ratio) ? ratio : 1));
  panX.value = 0;
  panY.value = 0;
}

function onWheel(event) {
  const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
  zoom.value = Math.min(6, Math.max(0.3, zoom.value * factor));
}

let dragging = false;
let dragStart = { x: 0, y: 0, panX: 0, panY: 0 };

function startDrag(event) {
  dragging = true;
  dragStart = { x: event.clientX, y: event.clientY, panX: panX.value, panY: panY.value };
}

function onDrag(event) {
  if (!dragging) return;
  panX.value = dragStart.panX + (event.clientX - dragStart.x) / zoom.value;
  panY.value = dragStart.panY + (event.clientY - dragStart.y) / zoom.value;
}

function endDrag() {
  dragging = false;
}

watch(
  () => route.query.checkpoint || "",
  (value) => {
    selectedCheckpoint.value = value;
  },
  { immediate: true },
);

watch(selectedCheckpoint, () => loadData());

watch(
  () => route.query.view,
  (value) => {
    if (viewOptions.includes(value)) view.value = value;
  },
);

function setView(value) {
  view.value = value;
  resetView();
  router.replace({ query: { ...route.query, view: value } });
}
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
const iterations = computed(() => progress.value?.iterations || []);
const islands = computed(() => progress.value?.islands || []);
const logTail = computed(() => progress.value?.log_tail || []);

// 进化树：按父子关系分层、向右展开（自绘连线，不用组件）
const TREE_COLUMN = 132;
const TREE_ROW = 42;

const treeLayout = computed(() => {
  const nodeList = nodes.value;
  const empty = { positions: new Map(), edges: [], width: chartWidth, height: 480 };
  if (!nodeList.length) return empty;

  const byId = new Map(nodeList.map((node) => [node.id, node]));
  const childrenOf = new Map();
  const hasParent = new Set();
  const edgeList = (data.value.edges || []).filter((edge) => byId.has(edge.source) && byId.has(edge.target));
  for (const edge of edgeList) {
    if (!childrenOf.has(edge.source)) childrenOf.set(edge.source, []);
    childrenOf.get(edge.source).push(edge.target);
    hasParent.add(edge.target);
  }

  const ordered = [...nodeList].sort((a, b) =>
    (a.generation || 0) - (b.generation || 0) ||
    (a.island || 0) - (b.island || 0) ||
    String(a.id).localeCompare(String(b.id)));
  const roots = ordered.filter((node) => !hasParent.has(node.id));
  const rootIds = roots.length ? roots.map((node) => node.id) : [ordered[0].id];

  const positions = new Map();
  const seen = new Set();
  let cursor = 0;

  const place = (id, depth) => {
    if (seen.has(id)) return positions.get(id);
    seen.add(id);
    const children = (childrenOf.get(id) || []).slice().sort((a, b) => {
      const left = byId.get(a);
      const right = byId.get(b);
      return (left?.generation || 0) - (right?.generation || 0) || String(a).localeCompare(String(b));
    });
    let y;
    if (children.length) {
      const childYs = children.map((child) => place(child, depth + 1)?.y).filter((value) => value != null);
      y = childYs.length ? childYs.reduce((sum, value) => sum + value, 0) / childYs.length : cursor * TREE_ROW + 46;
      if (!childYs.length) cursor += 1;
    } else {
      y = cursor * TREE_ROW + 46;
      cursor += 1;
    }
    const position = { x: 96 + depth * TREE_COLUMN, y };
    positions.set(id, position);
    return position;
  };

  for (const rootId of rootIds) place(rootId, 0);
  // 没有连线关系的孤立节点也要出现，按代数放在对应列
  for (const node of ordered) {
    if (positions.has(node.id)) continue;
    positions.set(node.id, { x: 96 + (Number(node.generation) || 0) * TREE_COLUMN, y: cursor * TREE_ROW + 46 });
    cursor += 1;
  }

  const values = [...positions.values()];
  return {
    positions,
    edges: edgeList.filter((edge) => positions.has(edge.source) && positions.has(edge.target)),
    width: Math.max(chartWidth, Math.max(...values.map((item) => item.x)) + 130),
    height: Math.max(380, Math.max(...values.map((item) => item.y)) + 52),
  };
});

// 正交折线：父 → 水平 → 垂直 → 子，比长贝塞尔短得多，也更像工程树图
function edgePath(source, target) {
  if (!source || !target) return "";
  const x1 = source.x + 12;
  const x2 = target.x - 12;
  const dy = target.y - source.y;
  if (Math.abs(dy) < 2) return `M ${x1} ${source.y} H ${x2}`;
  const dir = dy > 0 ? 1 : -1;
  const radius = Math.min(8, Math.abs(dy) / 2, Math.max(4, (x2 - x1) / 2));
  const midX = x1 + Math.max(radius + 2, (x2 - x1) / 2);
  return [
    `M ${x1} ${source.y}`,
    `H ${midX - radius}`,
    `Q ${midX} ${source.y} ${midX} ${source.y + dir * radius}`,
    `V ${target.y - dir * radius}`,
    `Q ${midX} ${target.y} ${midX + radius} ${target.y}`,
    `H ${x2}`,
  ].join(" ");
}

function nodePosition(id) {
  return treeLayout.value.positions.get(id);
}

// 节点填充=分数色阶，描边=岛屿；最优节点到根的整条路径描金
const scoreRange = computed(() => {
  const values = nodes.value
    .map((node) => Number(node.metrics?.[activeMetric.value]))
    .filter((value) => Number.isFinite(value));
  return values.length ? { min: Math.min(...values), max: Math.max(...values) } : { min: 0, max: 1 };
});

function scoreFill(node) {
  const score = Number(node.metrics?.[activeMetric.value]);
  if (!Number.isFinite(score)) return "#c3ccdb";
  const { min, max } = scoreRange.value;
  const ratio = max === min ? 1 : (score - min) / (max - min);
  return `hsl(${Math.round(212 - ratio * 152)} ${Math.round(28 + ratio * 46)}% ${Math.round(80 - ratio * 24)}%)`;
}

const parentIdOf = computed(() => {
  const map = new Map();
  for (const edge of data.value.edges || []) map.set(edge.target, edge.source);
  return map;
});

const elitePath = computed(() => {
  const path = new Set();
  let current = nodes.value.reduce((bestId, node) => {
    if (!bestId) return node.id;
    return scoreOfNode(node) > scoreOfNode(nodes.value.find((item) => item.id === bestId)) ? node.id : bestId;
  }, "");
  while (current && !path.has(current)) {
    path.add(current);
    current = parentIdOf.value.get(current);
  }
  return path;
});

function scoreOfNode(node) {
  const value = Number(node?.metrics?.[activeMetric.value]);
  return Number.isFinite(value) ? value : -Infinity;
}

const eliteEdges = computed(() => new Set(
  (data.value.edges || [])
    .filter((edge) => elitePath.value.has(edge.source) && elitePath.value.has(edge.target))
    .map((edge) => `${edge.source}->${edge.target}`),
));

function isEliteEdge(edge) {
  return eliteEdges.value.has(`${edge.source}->${edge.target}`);
}

// ── 悬浮提示：跟随鼠标显示节点详情，双击直接打开代码 ──
const hoverTip = ref(null);

function tipRows(entries) {
  return entries.filter(([, value]) => value !== null && value !== undefined && value !== "");
}

function showTip(event, entries) {
  hoverTip.value = { x: event.clientX, y: event.clientY, rows: tipRows(entries) };
}

function moveTip(event) {
  if (hoverTip.value) {
    hoverTip.value = { ...hoverTip.value, x: event.clientX, y: event.clientY };
  }
}

function hideTip() {
  hoverTip.value = null;
}

function tipForNode(node) {
  return [
    ["程序", String(node.id).slice(0, 8)],
    ["岛屿", node.island ?? "—"],
    ["代数", node.generation ?? "—"],
    ["发现轮次", node.iteration_found ?? "—"],
    [activeMetric.value, scoreLabel(node)],
    ["父程序", node.parent_id ? String(node.parent_id).slice(0, 8) : "初始程序"],
    ["操作", "单击选中 · 双击查看代码"],
  ];
}

function tipForIteration(item) {
  return [
    ["轮次", `第 ${item.iteration} 轮`],
    ["程序", String(item.program_id).slice(0, 8)],
    ["父程序", item.parent_id ? String(item.parent_id).slice(0, 8) : "初始程序"],
    ["状态", item.status === "completed" ? "成功" : "失败"],
    ["分数", iterationScore(item) === null ? "—" : iterationScore(item).toFixed(3)],
    ["耗时", item.duration_seconds ? `${Number(item.duration_seconds).toFixed(1)}s` : "—"],
    ["错误", item.error || ""],
    ["操作", "双击查看代码"],
  ];
}

// ── 轮次树：每一轮一个节点，横轴=轮次序号，从左到右完整展开 ──
const treeMode = ref("evolution");
const ITERATION_COLUMN = 104;
const ITERATION_ROW = 32;

function iterationScore(item) {
  const match = /combined_score=(-?[\d.]+)/.exec(item?.metrics_text || "");
  return match ? Number(match[1]) : null;
}

// 最优轮次来自日志里的 "🌟 New best solution found at iteration N: programId"
const bestEvents = computed(() => progress.value?.best_events || []);
const bestIterationNumber = computed(() => (
  bestEvents.value.length ? Math.max(...bestEvents.value.map((event) => Number(event.iteration) || 0)) : null
));
const bestProgramId = computed(() => bestEvents.value.at(-1)?.program_id || "");

function isBestIteration(item) {
  return Number(item.iteration) === bestIterationNumber.value && item.program_id === bestProgramId.value;
}

function iterationStroke(item) {
  if (isBestIteration(item)) return "#e8a63b";
  return item?.status === "completed" ? "#c3ccdb" : "#c14242";
}

function iterationScoreText(item) {
  const score = iterationScore(item);
  if (Number.isFinite(score)) return score.toFixed(2);
  return item?.status === "completed" ? "—" : "✕";
}

const iterationElitePath = computed(() => {
  const path = new Set();
  const byProgram = new Map(iterations.value.filter((item) => item?.program_id).map((item) => [item.program_id, item]));
  let current = iterations.value.find(isBestIteration)?.program_id;
  while (current && !path.has(current)) {
    path.add(current);
    current = byProgram.get(current)?.parent_id;
  }
  return path;
});
const iterationScoreRange = computed(() => {
  const values = iterations.value.map(iterationScore).filter((value) => Number.isFinite(value));
  return values.length ? { min: Math.min(...values), max: Math.max(...values) } : { min: 0, max: 1 };
});

function fillForIteration(item) {
  const score = iterationScore(item);
  if (!Number.isFinite(score)) return "#c3ccdb";
  const { min, max } = iterationScoreRange.value;
  const ratio = max === min ? 1 : (score - min) / (max - min);
  return `hsl(${Math.round(212 - ratio * 152)} ${Math.round(28 + ratio * 46)}% ${Math.round(80 - ratio * 24)}%)`;
}

const iterationTree = computed(() => {
  const list = iterations.value.filter((item) => item?.program_id);
  const empty = { rows: [], edges: [], width: chartWidth, height: 420 };
  if (!list.length) return empty;

  const byProgram = new Map(list.map((item) => [item.program_id, item]));
  const childrenOf = new Map();
  const hasParent = new Set();
  for (const item of list) {
    const parent = item.parent_id;
    if (parent && byProgram.has(parent)) {
      if (!childrenOf.has(parent)) childrenOf.set(parent, []);
      childrenOf.get(parent).push(item.program_id);
      hasParent.add(item.program_id);
    }
  }

  const positions = new Map();
  const seen = new Set();
  let cursor = 0;

  // x 按代际深度排列（不是按轮次序号）：父子永远只差一列，连线不会被拉长
  const place = (programId, depth) => {
    if (seen.has(programId)) return positions.get(programId);
    seen.add(programId);
    const item = byProgram.get(programId);
    const children = (childrenOf.get(programId) || []).slice()
      .sort((a, b) => (Number(byProgram.get(a)?.iteration) || 0) - (Number(byProgram.get(b)?.iteration) || 0));
    let y;
    if (children.length) {
      const ys = children.map((child) => place(child, depth + 1)?.y).filter((value) => value != null);
      y = ys.length ? ys.reduce((sum, value) => sum + value, 0) / ys.length : cursor * ITERATION_ROW + 46;
      if (!ys.length) cursor += 1;
    } else {
      y = cursor * ITERATION_ROW + 46;
      cursor += 1;
    }
    const position = { x: 96 + depth * ITERATION_COLUMN, y, item, depth };
    positions.set(programId, position);
    return position;
  };

  const roots = list
    .filter((item) => !hasParent.has(item.program_id))
    .sort((a, b) => (Number(a.iteration) || 0) - (Number(b.iteration) || 0));
  for (const root of roots) place(root.program_id, 0);
  for (const item of list) {
    if (positions.has(item.program_id)) continue;
    positions.set(item.program_id, { x: 96, y: cursor * ITERATION_ROW + 46, item, depth: 0 });
    cursor += 1;
  }

  const rows = [...positions.values()].sort((a, b) => a.x - b.x || a.y - b.y);
  const edges = list
    .filter((item) => item.parent_id && positions.has(item.parent_id))
    .map((item) => {
      const parent = positions.get(item.parent_id);
      const child = positions.get(item.program_id);
      return { source: parent, target: child, id: `${item.parent_id}-${item.program_id}` };
    });

  return {
    rows,
    edges,
    width: Math.max(chartWidth, Math.max(...rows.map((row) => row.x)) + 130),
    height: Math.max(340, Math.max(...rows.map((row) => row.y)) + 46),
  };
});

const activeTreeSize = computed(() => (treeMode.value === "iterations"
  ? { width: iterationTree.value.width, height: iterationTree.value.height }
  : { width: treeLayout.value.width, height: treeLayout.value.height }));

// 轮次树很宽（50 轮），若按容器宽度等比缩放会把节点压得看不清，
// 因此它按 1:1 像素渲染并交给容器横向滚动，缩放只改变 SVG 自身尺寸。
const chartStyle = computed(() => (treeMode.value === "iterations"
  ? {
      width: `${Math.round(iterationTree.value.width * zoom.value)}px`,
      height: `${Math.round(iterationTree.value.height * zoom.value)}px`,
    }
  : {}));

const chartTransform = computed(() => (treeMode.value === "iterations"
  ? `translate(${panX.value} ${panY.value})`
  : `translate(${panX.value} ${panY.value}) scale(${zoom.value})`));

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

function programLink(id) {
  const query = { run_id: runId.value };
  if (selectedCheckpoint.value) query.checkpoint = selectedCheckpoint.value;
  return { path: `/program/${encodeURIComponent(id)}`, query };
}

function isHighlighted(node) {
  if (highlight.value === "top") {
    // 所有节点分数并列时不做高亮，否则整张图都会被涂成金色
    if (new Set(scoreValues.value).size < 2) return false;
    return bestScore.value !== null && Number(node.metrics?.[activeMetric.value]) === bestScore.value;
  }
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

const metricRanges = computed(() => {
  const ranges = {};
  for (const node of nodes.value) {
    for (const [name, value] of Object.entries(node.metrics || {})) {
      if (typeof value !== "number" || !Number.isFinite(value)) continue;
      const range = ranges[name] || (ranges[name] = { min: Infinity, max: -Infinity });
      range.min = Math.min(range.min, value);
      range.max = Math.max(range.max, value);
    }
  }
  return ranges;
});

function numericMetrics(node) {
  return Object.entries(node.metrics || {})
    .filter(([name, value]) => name !== activeMetric.value)
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value));
}

function formatMetric(value) {
  return value.toFixed(4);
}

function barRatio(node, name) {
  const value = node.metrics?.[name];
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  const range = metricRanges.value[name];
  if (!range) return 0;
  if (range.max === range.min) return 1;
  return Math.max(0, Math.min(1, (value - range.min) / (range.max - range.min)));
}

function scoreRatio(value) {
  const range = metricRanges.value[activeMetric.value];
  if (typeof value !== "number" || !Number.isFinite(value) || !range) return 0;
  if (range.max === range.min) return 1;
  return Math.max(0, Math.min(1, (value - range.min) / (range.max - range.min)));
}

function metricScale(name) {
  const range = metricRanges.value[name];
  if (!range) return { min: "0.00", max: "1.00" };
  return { min: Number(range.min).toFixed(2), max: Number(range.max).toFixed(2) };
}

function barColor(ratio) {
  return `hsl(213 72% ${Math.round(62 - ratio * 22)}%)`;
}

async function loadData() {
  try {
    error.value = "";
    const response = await api.evolution(route.query.run_id || "", selectedCheckpoint.value);
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

// ── 点击节点弹出代码抽屉：先查当前检查点，再跨检查点兜底 ──
const codeDrawer = ref(null);
const drawerLines = computed(() => String(codeDrawer.value?.code || "").split("\n"));

// 复制抽屉里的完整代码：'' | 'done' | 'fail'，1.6s 后复原
const drawerCopyState = ref("");
let drawerCopyTimer;

async function copyDrawerCode() {
  const text = String(codeDrawer.value?.code || "");
  if (!text) return;
  let ok = true;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    ok = false;
  }
  drawerCopyState.value = ok ? "done" : "fail";
  window.clearTimeout(drawerCopyTimer);
  drawerCopyTimer = window.setTimeout(() => {
    drawerCopyState.value = "";
  }, 1600);
}

function isBestProgramId(id) {
  return !!bestProgramId.value && id === bestProgramId.value;
}

// 耗时按 program_id 从每轮明细里取（轮次树的 item 自带 duration_seconds）
const durationByProgram = computed(() => {
  const map = new Map();
  for (const item of iterations.value) {
    if (item?.program_id && item.duration_seconds != null) {
      map.set(item.program_id, Number(item.duration_seconds));
    }
  }
  return map;
});

function drawerDurationLabel(drawer) {
  const seconds = drawer?.duration ?? durationByProgram.value.get(drawer?.id);
  return Number.isFinite(seconds) ? `${Number(seconds).toFixed(1)}s` : "—";
}

const drawerMetrics = computed(() => Object.entries(codeDrawer.value?.node?.metrics || {}));

// 「在代码目录中打开」：最优程序指向 best 产物，其它指向所在检查点的程序目录
const drawerCodePath = computed(() => {
  const drawer = codeDrawer.value;
  if (!drawer) return "";
  if (isBestProgramId(drawer.id)) return "output/best/best_program.py";
  if (drawer.checkpoint && drawer.checkpoint !== "最新") {
    return `output/checkpoints/${drawer.checkpoint}/programs/${drawer.id}.json`;
  }
  return "output";
});

async function loadProgramCode(id, meta = {}) {
  codeDrawer.value = { id, ...meta, code: "", error: "", loading: true };
  const local = nodes.value.find((item) => item.id === id);
  if (local?.code) {
    codeDrawer.value = {
      ...codeDrawer.value,
      node: local,
      code: local.code,
      checkpoint: selectedCheckpoint.value || "最新",
      loading: false,
    };
    return;
  }
  try {
    const response = await api.runProgram(runId.value, id);
    if (response?.node) {
      codeDrawer.value = {
        ...codeDrawer.value,
        node: response.node,
        code: response.node.code || "",
        checkpoint: response.checkpoint || "",
        loading: false,
      };
      return;
    }
  } catch {
    /* 接口没就绪或没找到，走下面的兜底 */
  }
  for (const item of [...checkpoints.value].reverse()) {
    if (!item?.name) continue;
    try {
      const detail = await api.evolution(runId.value, item.name);
      const hit = (detail.nodes || []).find((node) => node.id === id);
      if (hit) {
        codeDrawer.value = { ...codeDrawer.value, node: hit, code: hit.code || "", checkpoint: item.name, loading: false };
        return;
      }
    } catch {
      /* 跳过读不出来的检查点 */
    }
  }
  codeDrawer.value = {
    ...codeDrawer.value,
    loading: false,
    error: "这个程序只存在于更早的检查点里，当前读不到它的代码。可点下方「打开完整程序页」尝试。",
  };
}

function openCode(id, meta) {
  if (id) loadProgramCode(id, meta);
}

function closeCode() {
  codeDrawer.value = null;
  drawerCopyState.value = "";
  window.clearTimeout(drawerCopyTimer);
}

const listContainer = ref(null);

async function scrollToIndex(index) {
  await nextTick();
  const rows = listContainer.value?.querySelectorAll(".program-row");
  if (rows && index >= 0 && index < rows.length) {
    rows[index].scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

async function moveToNode(id) {
  selectedId.value = id;
  await scrollToIndex(visibleNodes.value.findIndex((node) => node.id === id));
}

async function moveSelection(offset) {
  if (!visibleNodes.value.length) return;
  const current = visibleNodes.value.findIndex((node) => node.id === selectedId.value);
  const next = current === -1
    ? 0
    : Math.min(visibleNodes.value.length - 1, Math.max(0, current + offset));
  selectedId.value = visibleNodes.value[next].id;
  await scrollToIndex(next);
}

function onListKeydown(event) {
  if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
  event.preventDefault();
  moveSelection(event.key === "ArrowUp" ? -1 : 1);
}

function openProgram(node) {
  const run = route.query.run_id ? `?run_id=${encodeURIComponent(route.query.run_id)}` : "";
  const location = router.resolve(`/program/${encodeURIComponent(node.id)}${run}`);
  window.open(location.href, "_blank", "noopener");
}

// 双击在当前页打开代码（window.open 常被浏览器拦截，且用户期望"点了就看"）
function openProgramHere(id) {
  if (!id) return;
  router.push(programLink(id));
}

function parentOf(node) {
  return nodes.value.find((item) => item.id === node.parent_id);
}

function childNodes(node) {
  return nodes.value.filter((item) => item.parent_id === node.id);
}

function hasScore(node) {
  const value = node?.metrics?.[activeMetric.value];
  return typeof value === "number" && Number.isFinite(value);
}

function islandColor(node) {
  return `hsl(${(Number(node.island || 0) * 67 + 210) % 360} 58% 55%)`;
}

const trendGroupHeight = 300;

const trendGroups = computed(() => {
  const buckets = !showIslands.value || islandIds.value.length <= 1
    ? [{ key: "all", label: "", nodes: nodes.value }]
    : islandIds.value.map((island) => ({
        key: `island-${island}`,
        label: `岛屿 ${island}`,
        nodes: nodes.value.filter((node) => Number(node.island) === Number(island)),
      }));
  return buckets.map((bucket) => ({
    ...bucket,
    scored: bucket.nodes.filter((node) => hasScore(node)),
    naNodes: bucket.nodes.filter((node) => !hasScore(node)),
  }));
});

const trendHeight = computed(() => Math.max(320, trendGroups.value.length * trendGroupHeight + 40));

function metricY(node) {
  const value = node.metrics?.[activeMetric.value];
  const min = Math.min(...scoreValues.value);
  const max = Math.max(...scoreValues.value);
  if (!Number.isFinite(value)) return 280;
  if (min === max) return 130;
  return 230 - ((value - min) / (max - min)) * 190;
}

function metricX(node) {
  return 70 + ((Number(node.generation) || 0) / Math.max(maxGeneration.value, 1)) * 950;
}

function saveMetric() {
  localStorage.setItem("openevolve.metric", metric.value);
}

function onKeydown(event) {
  if (event.key === "Escape" && codeDrawer.value) {
    codeDrawer.value = null;
  }
}

onMounted(() => {
  loadData();
  loadProgress();
  refreshTimer = window.setInterval(loadData, 3000);
  progressTimer = window.setInterval(loadProgress, 3000);
  window.addEventListener("keydown", onKeydown);
});
onUnmounted(() => {
  window.clearInterval(refreshTimer);
  window.clearInterval(progressTimer);
  window.removeEventListener("keydown", onKeydown);
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
        <button v-for="item in [['tree','进化树'],['iterations','轮次明细'],['trend','性能趋势'],['list','程序列表'],['map','特征地图']]" :key="item[0]" class="button" :class="{ selected: view === item[0] }" @click="setView(item[0])">{{ item[1] }}</button>
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
      <label v-if="checkpoints.length" class="checkpoint-control">检查点
        <select v-model="selectedCheckpoint" class="select-input">
          <option value="">最新</option>
          <option v-for="item in checkpoints" :key="item.name" :value="item.name">{{ item.name }} · {{ item.programs }} 程序</option>
        </select>
      </label>
      <span class="refresh-label"><span class="live-dot"></span>每 3 秒刷新</span>
    </div>

    <p v-if="progressError" class="message error">{{ progressError }}</p>
    <section v-if="progress" class="card run-progress">
      <div class="progress-head">
        <div>
          <p class="eyebrow">Run Progress</p>
          <h2>{{ runId }}</h2>
        </div>
        <div class="progress-head-right">
          <span class="status-badge" :class="progress.status">{{ statusLabels[progress.status] || progress.status }}</span>
          <RouterLink v-if="runId" class="button" :to="{ path: `/runs/${runId}` }">任务详情与日志</RouterLink>
        </div>
      </div>
      <div class="progress-bar-track" role="progressbar" :aria-valuenow="progressPercent" aria-valuemin="0" aria-valuemax="100">
        <div class="progress-bar-fill" :class="progress.status" :style="{ width: `${progressPercent}%` }"></div>
      </div>
      <p class="progress-last">
        第 {{ progress.finished_iterations }} / {{ progress.total_iterations }} 轮 ·
        成功 {{ progress.completed_iterations }} · 失败 {{ progress.failed_iterations }}
      </p>
      <p v-if="progress.status === 'running' && !progress.has_checkpoint" class="progress-hint">
        正在运行。检查点按固定轮次间隔保存，生成之前下方图表不会出现程序节点。
      </p>
    </section>

    <p v-if="error" class="message error">{{ error }}</p>
    <div v-else-if="loading" class="card empty-state">正在读取进化数据…</div>
    <div v-else-if="!nodes.length" class="card empty-state">
      <p><strong>当前没有可可视化的检查点数据。</strong></p>
      <p v-if="runId" class="empty-hint">任务 {{ runId }} 还没有生成 checkpoint。检查点按固定轮次间隔保存，第一轮结束前这里不会有节点；若任务已经失败或被中断，也不会有数据。</p>
      <p v-else class="empty-hint">没有指定任务。请从任务列表点「查看」进入某个任务的可视化。</p>
      <div class="empty-actions">
        <RouterLink class="button" to="/runs">返回任务列表</RouterLink>
        <RouterLink v-if="runId" class="button" :to="{ path: `/runs/${runId}` }">查看任务详情与日志</RouterLink>
      </div>
    </div>

    <div v-else-if="view === 'tree'" class="workspace">
      <section class="card chart-card">
        <div class="chart-heading">
          <div>
            <h2>{{ treeMode === "iterations" ? "轮次演化树" : "程序演化关系" }}</h2>
            <p v-if="treeMode === 'iterations'">每一轮一个节点，横轴=代际层级（父子相邻一列，连线不被拉长），方块内是轮次号、右侧是分数；红色=该轮失败，★金色=最优轮次及其进化路径 · 滚轮缩放，按住拖拽平移</p>
            <p v-else>方块内数字=岛屿编号，填充=分数（蓝→绿→黄由低到高），描边颜色=所属岛屿，金色=最优节点到根的进化路径 · 滚轮缩放，按住拖拽平移</p>
          </div>
          <div class="zoom-controls">
            <button class="button zoom-button" :class="{ selected: treeMode === 'evolution' }" @click="treeMode = 'evolution'; resetView()">按演化关系</button>
            <button class="button zoom-button" :class="{ selected: treeMode === 'iterations' }" @click="treeMode = 'iterations'; resetView()">按轮次</button>
            <span class="count-pill">{{ treeMode === "iterations" ? `${iterationTree.rows.length} 轮` : `${nodes.length} 个程序 · ${maxGeneration + 1} 代` }}</span>
            <span v-if="bestIterationNumber" class="best-pill" title="日志中最后一次刷新最优解的轮次">
              ★ 最优：第 {{ bestIterationNumber }} 轮 · {{ String(bestProgramId).slice(0, 8) }}
            </span>
            <button class="button zoom-button" @click="zoom = Math.min(6, zoom * 1.2)">＋</button>
            <button class="button zoom-button" @click="zoom = Math.max(0.3, zoom / 1.2)">－</button>
            <button class="button zoom-button" title="缩放到完整显示整棵树" @click="fitView">适应</button>
            <button class="button zoom-button" @click="resetView">{{ Math.round(zoom * 100) }}%</button>
          </div>
        </div>
        <div ref="scrollBox" class="svg-scroll">
          <svg :viewBox="`0 0 ${activeTreeSize.width} ${activeTreeSize.height}`" class="tree-chart" :style="chartStyle" role="img" :aria-label="treeMode === 'iterations' ? '轮次演化树' : '程序演化树'" @wheel.prevent="onWheel" @mousedown="startDrag" @mousemove="onDrag" @mouseup="endDrag" @mouseleave="endDrag">
            <g :transform="chartTransform">
              <template v-if="treeMode === 'iterations'">
                <path
                  v-for="edge in iterationTree.edges"
                  :key="edge.id"
                  :d="edgePath(edge.source, edge.target)"
                  class="tree-edge compact"
                  :class="{ failed: edge.target.item.status !== 'completed', elite: iterationElitePath.has(edge.target.item.program_id) }"
                />
                <g
                  v-for="row in iterationTree.rows"
                  :key="row.item.program_id"
                  class="tree-node"
                  @click="openCode(row.item.program_id, { iteration: row.item.iteration, score: iterationScore(row.item), status: row.item.status, duration: row.item.duration_seconds })"
                  @dblclick="openProgramHere(row.item.program_id)"
                  @mouseenter="showTip($event, tipForIteration(row.item))"
                  @mousemove="moveTip"
                  @mouseleave="hideTip"
                >
                  <title>第 {{ row.item.iteration }} 轮 · {{ row.item.program_id }} · {{ row.item.status === "completed" ? "成功" : "失败" }} · 分数 {{ iterationScore(row.item) ?? "—" }}{{ isBestIteration(row.item) ? " · 最优轮次" : "" }}</title>
                  <circle v-if="isBestIteration(row.item)" :cx="row.x" :cy="row.y" r="19" class="best-halo" />
                  <rect
                    :x="row.x - 12"
                    :y="row.y - 12"
                    width="24"
                    height="24"
                    rx="7"
                    :fill="fillForIteration(row.item)"
                    :stroke="iterationStroke(row.item)"
                    :stroke-width="isBestIteration(row.item) ? 2.4 : 1.4"
                  />
                  <text v-if="isBestIteration(row.item)" :x="row.x" :y="row.y - 18" class="best-star" text-anchor="middle">★</text>
                  <text :x="row.x" :y="row.y + 3.6" class="node-badge" text-anchor="middle">{{ row.item.iteration }}</text>
                  <text :x="row.x + 16" :y="row.y + 3.5" class="node-score compact" :class="{ failed: row.item.status !== 'completed', best: isBestIteration(row.item) }">{{ iterationScoreText(row.item) }}</text>
                </g>
              </template>
              <template v-else>
              <path
                v-for="edge in treeLayout.edges"
                :key="`${edge.source}-${edge.target}`"
                :d="edgePath(nodePosition(edge.source), nodePosition(edge.target))"
                class="tree-edge"
                :class="{ chosen: selectedId && (selectedId === edge.source || selectedId === edge.target), elite: isEliteEdge(edge) }"
              />
              <g
                v-for="node in nodes"
                :key="node.id"
                class="tree-node"
                :class="{ chosen: selectedId === node.id, highlighted: isHighlighted(node) }"
                @click="selectNode(node.id); openCode(node.id)"
                @dblclick="openProgramHere(node.id)"
                @mouseenter="showTip($event, tipForNode(node))"
                @mousemove="moveTip"
                @mouseleave="hideTip"
              >
                <title>{{ node.id }} · 岛屿 {{ node.island }} · 第 {{ node.generation }} 代 · {{ activeMetric }}: {{ scoreLabel(node) }}</title>
                <rect
                  :x="nodePosition(node.id)?.x - 13"
                  :y="nodePosition(node.id)?.y - 13"
                  width="26"
                  height="26"
                  rx="9"
                  :fill="isHighlighted(node) ? '#e8a63b' : scoreFill(node)"
                  :stroke="elitePath.has(node.id) ? '#e8a63b' : (selectedId === node.id ? '#315fce' : islandColor(node))"
                  :stroke-width="elitePath.has(node.id) ? 3.5 : (selectedId === node.id ? 2.5 : 2)"
                />
                <text :x="nodePosition(node.id)?.x" :y="nodePosition(node.id)?.y + 4" class="node-badge" text-anchor="middle">{{ node.island }}</text>
                <text :x="nodePosition(node.id)?.x + 20" :y="nodePosition(node.id)?.y - 2" class="node-title">
                  轮{{ node.iteration_found ?? "—" }} · {{ String(node.id).slice(0, 8) }}
                </text>
                <text :x="nodePosition(node.id)?.x + 20" :y="nodePosition(node.id)?.y + 11" class="node-score">{{ scoreLabel(node) }}</text>
              </g>
              </template>
            </g>
          </svg>
        </div>
      </section>
      <aside v-if="selected" class="card detail-card">
        <NodeDetail :node="selected" :nodes="nodes" :archive="data.archive || []" :metric="activeMetric" @close="selectedId = ''" @open="openProgram" />
      </aside>
    </div>

    <section v-else-if="view === 'iterations'" class="card chart-card">
      <div class="chart-heading">
        <div><h2>演化岛屿与轮次明细</h2><p>各岛屿精英概览 + 每轮的父子关系、耗时与指标</p></div>
        <span class="count-pill">{{ iterations.length }} 轮 · {{ islands.length }} 个岛屿</span>
      </div>

      <div v-if="islands.length" class="island-grid">
        <article v-for="island in islands" :key="island.index" class="island-card">
          <header><strong>岛屿 {{ island.index }}</strong><span>第 {{ island.generation }} 代</span></header>
          <div class="island-stats">
            <div><small>程序</small><b>{{ island.programs }}</b></div>
            <div><small>最高分</small><b>{{ Number(island.best_score).toFixed(4) }}</b></div>
            <div><small>平均分</small><b>{{ Number(island.avg_score).toFixed(4) }}</b></div>
            <div><small>多样性</small><b>{{ Number(island.diversity).toFixed(2) }}</b></div>
          </div>
          <p v-if="island.best_program_id" class="island-best">最优 <code>{{ island.best_program_id }}</code></p>
        </article>
      </div>

      <p v-if="!iterations.length" class="empty-hint">还没有解析到完成的轮次。</p>
      <div v-else class="table-scroll">
        <table class="iteration-table">
          <thead>
            <tr><th>轮次</th><th>状态</th><th>岛屿</th><th>程序</th><th>父程序</th><th>耗时</th><th>指标</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in iterations" :key="item.iteration" :class="{ 'best-row': isBestIteration(item) }">
              <td>
                {{ item.iteration }}
                <span v-if="isBestIteration(item)" class="best-badge" title="最优轮次">★ 最优</span>
              </td>
              <td>
                <span class="iteration-state" :class="item.status">{{ item.status === "completed" ? "成功" : "失败" }}</span>
              </td>
              <td>{{ item.island ?? "—" }}</td>
              <td class="mono">
                <RouterLink v-if="item.program_id" :to="programLink(item.program_id)">{{ item.program_id.slice(0, 8) }}</RouterLink>
                <span v-else>—</span>
              </td>
              <td class="mono">
                <RouterLink v-if="item.parent_id" :to="programLink(item.parent_id)">{{ item.parent_id.slice(0, 8) }}</RouterLink>
                <span v-else>—</span>
              </td>
              <td>{{ item.duration_seconds ? `${Number(item.duration_seconds).toFixed(1)}s` : "—" }}</td>
              <td class="mono metrics-cell">{{ item.metrics_text || item.error || "—" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-else-if="view === 'trend'" class="card chart-card">
      <div class="chart-heading">
        <div><h2>指标趋势</h2><p>{{ activeMetric }} · 按代数查看分数变化{{ trendGroups.length > 1 ? " · 每个岛屿一张子图" : "" }}</p></div>
        <div class="chart-heading-right">
          <span class="count-pill">最高 {{ bestScore === null ? "—" : bestScore.toFixed(4) }}</span>
          <button class="button zoom-button" @click="resetView">{{ Math.round(zoom * 100) }}%</button>
        </div>
      </div>
      <div class="svg-scroll">
        <svg :viewBox="`0 0 1120 ${trendHeight}`" class="trend-chart" role="img" aria-label="指标趋势散点图" @wheel.prevent="onWheel" @mousedown="startDrag" @mousemove="onDrag" @mouseup="endDrag" @mouseleave="endDrag">
          <g :transform="`translate(${panX} ${panY}) scale(${zoom})`">
            <g v-for="(group, index) in trendGroups" :key="group.key" :transform="`translate(0 ${index * trendGroupHeight})`">
              <text v-if="group.label" x="14" y="22" class="trend-group-title">{{ group.label }}（{{ group.nodes.length }} 个程序）</text>
              <line x1="60" y1="240" x2="1090" y2="240" class="chart-axis" />
              <line x1="60" y1="30" x2="60" y2="240" class="chart-axis" />
              <text x="575" y="266" text-anchor="middle" class="axis-label">进化代数</text>
              <g v-for="node in group.scored" :key="node.id" class="trend-point" :class="{ highlighted: isHighlighted(node), chosen: selectedId === node.id }" @click="selectNode(node.id)" @dblclick="openProgram(node)">
                <line v-if="parentOf(node) && hasScore(parentOf(node))" :x1="metricX(parentOf(node))" :y1="metricY(parentOf(node))" :x2="metricX(node)" :y2="metricY(node)" class="trend-edge" />
                <circle :cx="metricX(node)" :cy="metricY(node)" r="6" :fill="islandColor(node)" />
                <title>{{ node.id }} · 第 {{ node.generation }} 代 · {{ activeMetric }}: {{ scoreLabel(node) }}</title>
              </g>
              <g v-if="group.naNodes.length" class="trend-na">
                <rect x="60" y="278" width="1030" height="24" rx="6" />
                <text x="70" y="294">无有效指标：{{ group.naNodes.length }} 个程序</text>
              </g>
            </g>
          </g>
        </svg>
      </div>
      <p v-if="selected" class="selected-caption">已选择 {{ selected.id }} · {{ activeMetric }}: {{ scoreLabel(selected) }}</p>
    </section>

    <section v-else-if="view === 'map'" class="card chart-card">
      <div class="chart-heading">
        <div><h2>MAP-Elites 特征地图</h2><p>每个格子是该特征区间内的最优程序；颜色越深表示 fitness 越高</p></div>
        <span class="count-pill">{{ featureMaps.length }} 个岛屿</span>
      </div>
      <div v-if="featureMaps.length" class="map-grid">
        <article v-for="grid in featureMaps" :key="grid.island" class="map-card">
          <header><strong>岛屿 {{ grid.island }}</strong><span>{{ (grid.dimensions || []).join(" / ") }}</span></header>
          <svg :viewBox="`0 0 ${gridGeometry(grid).width} ${gridGeometry(grid).height}`" class="map-svg" role="img" aria-label="MAP-Elites 特征网格">
            <rect
              v-for="cell in grid.cells"
              :key="`${cell.x}-${cell.y}`"
              :x="cellRect(cell, gridGeometry(grid), grid).x"
              :y="cellRect(cell, gridGeometry(grid), grid).y"
              :width="gridGeometry(grid).size - 1"
              :height="gridGeometry(grid).size - 1"
              :fill="cellColor(cell)"
              class="map-cell"
              :class="{ chosen: selectedId === cell.program_id }"
              @click="selectNode(cell.program_id)"
            />
          </svg>
          <p class="map-hint">{{ grid.bins_x }} × {{ grid.bins_y }} 格 · 已占用 {{ grid.cells.length }}</p>
        </article>
      </div>
      <p v-else class="empty-state">当前检查点还没有 MAP-Elites 网格数据。</p>
      <p v-if="selected" class="selected-caption">已选择 {{ selected.id }} · {{ activeMetric }}: {{ scoreLabel(selected) }}</p>
    </section>

    <section v-else class="card list-card">
      <div class="list-toolbar">
        <input v-model="search" class="text-input" placeholder="搜索程序 ID…" aria-label="搜索程序 ID">
        <select v-model="sort" class="select-input" aria-label="排序方式">
          <option value="score">按指标排序</option><option value="generation">按代数</option><option value="island">按岛屿</option><option value="id">按 ID</option>
        </select>
      </div>
      <div class="summary-row">
        <div><small>🏆 最高分</small><strong>{{ bestScore === null ? "—" : bestScore.toFixed(4) }}</strong><span class="summary-bar"><i :style="{ width: `${scoreRatio(bestScore) * 100}%` }"></i></span></div>
        <div><small>📊 平均分</small><strong>{{ averageScore === null ? "—" : averageScore.toFixed(4) }}</strong><span class="summary-bar"><i :style="{ width: `${scoreRatio(averageScore) * 100}%` }"></i></span></div>
        <div><small>总计</small><strong>{{ nodes.length }} 程序 · {{ maxGeneration + 1 }} 代 · {{ islandIds.length }} 岛屿</strong></div>
        <div><small>结果</small><strong>{{ visibleNodes.length }} / {{ nodes.length }}</strong></div>
      </div>
      <div ref="listContainer" class="program-list" tabindex="0" aria-label="程序列表，可用上下方向键切换" @keydown="onListKeydown">
        <article v-for="(node, index) in visibleNodes" :key="node.id" class="program-row" :class="{ active: selectedId === node.id, highlighted: isHighlighted(node), elite: elitePath.has(node.id) }" @click="selectNode(node.id)">
          <span class="program-rank">#{{ index + 1 }}</span>
          <div class="program-main">
            <div class="score-line">
              <span class="score-name">{{ activeMetric }}:</span>
              <b class="mono">{{ scoreLabel(node) }}</b>
              <span v-if="elitePath.has(node.id)" class="elite-badge" title="位于最优节点到根的进化路径上">最优路径</span>
              <span class="score-bar"><i :style="{ width: `${barRatio(node, activeMetric) * 100}%`, background: barColor(barRatio(node, activeMetric)) }"></i></span>
            </div>
            <dl class="program-facts">
              <div><dt>ID</dt><dd class="mono">{{ node.id }}</dd></div>
              <div><dt>代数</dt><dd>{{ node.generation ?? "—" }}</dd></div>
              <div><dt>岛屿</dt><dd>{{ node.island ?? "—" }}</dd></div>
              <div><dt>父程序</dt><dd><button v-if="node.parent_id" class="parent-link" title="选择并定位父程序" @click.stop="moveToNode(node.parent_id)">{{ node.parent_id }}</button><span v-else>无</span></dd></div>
            </dl>
          </div>
          <div v-if="numericMetrics(node).length" class="metric-bars">
            <div v-for="[name, value] in numericMetrics(node)" :key="name" class="metric-bar">
              <span class="metric-name mono">{{ name }}</span>
              <span class="metric-value mono">{{ formatMetric(value) }}</span>
              <span class="metric-track"><i :style="{ width: `${barRatio(node, name) * 100}%`, background: barColor(barRatio(node, name)) }"></i></span>
              <span class="metric-scale"><em>{{ metricScale(name).min }}</em><em>{{ metricScale(name).max }}</em></span>
            </div>
          </div>
          <button class="button open-window" title="在新窗口打开完整程序页" @click.stop="openProgram(node)">在新窗口打开</button>
        </article>
      </div>
      <NodeDetail v-if="selected" :node="selected" :nodes="nodes" :archive="data.archive || []" :metric="activeMetric" @close="selectedId = ''" @open="openProgram" />
    </section>

    <div v-if="hoverTip" class="node-tip" :style="{ left: `${hoverTip.x + 16}px`, top: `${hoverTip.y + 16}px` }">
      <div v-for="[label, value] in hoverTip.rows" :key="label" class="tip-row">
        <small>{{ label }}</small><b>{{ value }}</b>
      </div>
    </div>

    <div v-if="codeDrawer" class="code-drawer">
      <header class="drawer-head">
        <div>
          <p class="eyebrow">Code Preview</p>
          <h3>
            <span v-if="codeDrawer.iteration">第 {{ codeDrawer.iteration }} 轮 · </span>{{ String(codeDrawer.id).slice(0, 8) }}
            <span v-if="isBestProgramId(codeDrawer.id)" class="best-badge">★ 最优</span>
          </h3>
          <p class="drawer-meta mono">
            {{ codeDrawer.checkpoint || "—" }}
            <span v-if="codeDrawer.score != null">· 分数 {{ Number(codeDrawer.score).toFixed(3) }}</span>
            <span v-if="!codeDrawer.loading && !codeDrawer.error"> · {{ drawerLines.length }} 行</span>
          </p>
        </div>
        <div class="drawer-head-actions">
          <button
            class="button drawer-copy"
            :class="drawerCopyState"
            :disabled="codeDrawer.loading || !!codeDrawer.error || !codeDrawer.code"
            :title="codeDrawer.code ? '复制完整代码到剪贴板' : '暂无可复制的代码'"
            @click="copyDrawerCode"
          >{{ drawerCopyState === "done" ? "已复制" : (drawerCopyState === "fail" ? "复制失败" : "复制代码") }}</button>
          <button class="button" @click="closeCode">✕</button>
        </div>
      </header>

      <dl class="drawer-facts">
        <div><dt>岛屿</dt><dd>{{ codeDrawer.node?.island ?? "—" }}</dd></div>
        <div><dt>代数</dt><dd>{{ codeDrawer.node?.generation ?? "—" }}</dd></div>
        <div><dt>发现轮次</dt><dd>{{ codeDrawer.iteration ?? codeDrawer.node?.iteration_found ?? "—" }}</dd></div>
        <div><dt>耗时</dt><dd>{{ drawerDurationLabel(codeDrawer) }}</dd></div>
        <div><dt>状态</dt><dd :class="{ 'fact-bad': codeDrawer.status === 'failed' }">{{ codeDrawer.status === "failed" ? "失败" : (codeDrawer.status === "completed" ? "成功" : "—") }}</dd></div>
        <div><dt>父程序</dt><dd class="mono">{{ codeDrawer.node?.parent_id ? String(codeDrawer.node.parent_id).slice(0, 8) : "—" }}</dd></div>
      </dl>

      <div v-if="drawerMetrics.length" class="drawer-metrics">
        <span v-for="[name, value] in drawerMetrics" :key="name">
          <small>{{ name }}</small>
          <b>{{ typeof value === "number" ? value.toFixed(4) : value }}</b>
        </span>
      </div>

      <p v-if="codeDrawer.loading" class="drawer-hint">正在读取代码…</p>
      <p v-else-if="codeDrawer.error" class="message error">{{ codeDrawer.error }}</p>
      <div v-else class="code-view drawer-code">
        <div class="line-numbers">
          <span v-for="(_, index) in drawerLines" :key="index">{{ index + 1 }}</span>
        </div>
        <pre class="code-body"><code><span v-for="(line, index) in drawerLines" :key="index" class="code-line" v-html="highlightLine(line)"></span></code></pre>
      </div>

      <footer class="drawer-foot">
        <RouterLink class="button primary" :to="programLink(codeDrawer.id)">打开完整程序页</RouterLink>
        <RouterLink class="button" :to="{ path: '/code', query: { run_id: runId, path: drawerCodePath } }">在代码目录中打开</RouterLink>
        <button class="button" @click="closeCode">关闭</button>
      </footer>
    </div>
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
.progress-head-right { display:flex; align-items:center; gap:10px; }
.progress-toggle { min-height:28px; padding:3px 10px; font-size:11px; }
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
.island-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(190px, 1fr)); gap:10px; margin-top:14px; }
.island-card { padding:12px; border:1px solid var(--line); border-radius:10px; background:#fafbfe; }
.island-card header { display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
.island-card header strong { font-size:12px; }
.island-card header span { color:var(--muted); font-size:10px; }
.island-stats { display:grid; grid-template-columns:repeat(2, 1fr); gap:8px; margin-top:10px; }
.island-stats > div { display:flex; flex-direction:column; gap:2px; }
.island-stats small { color:var(--muted); font-size:10px; }
.island-stats b { font-size:12px; }
.island-best { margin:10px 0 0; color:var(--muted); font-size:10px; overflow-wrap:anywhere; }
.island-best code { font-size:10px; }
.log-panel { margin-top:14px; border:1px solid var(--line); border-radius:10px; overflow:hidden; }
.log-panel summary { padding:10px 12px; background:#f8f9fc; color:#68748a; font-size:11px; cursor:pointer; }
.log-panel pre { max-height:280px; margin:0; overflow:auto; padding:12px; background:#172033; color:#d7e0f1; font:10px/1.5 "IBM Plex Mono", monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
.view-switch { display:flex; gap:5px; padding:4px; border:1px solid var(--line); border-radius:10px; background:#fff; }
.view-switch .button { min-height:34px; border:0; background:transparent; box-shadow:none; }
.view-switch .button.selected { background:var(--accent-soft); color:var(--accent); }
.metric-controls { display:flex; align-items:center; flex-wrap:wrap; gap:14px 24px; padding:12px 16px; margin-bottom:18px; }
.metric-controls label { display:flex; align-items:center; gap:9px; color:var(--muted); font-size:12px; font-weight:600; }
.metric-controls .select-input { width:auto; min-width:160px; min-height:34px; padding:6px 10px; font-size:12px; }
.check-control input { accent-color:var(--accent); }
.refresh-label { display:flex; align-items:center; gap:7px; margin-left:auto; color:var(--muted); font-size:11px; }
.live-dot { width:7px; height:7px; border-radius:50%; background:#37a775; box-shadow:0 0 0 3px #37a77520; }
/* 图表占满整行，与上方控制栏同宽；选中程序的详情放到图表下方 */
.workspace { display:grid; grid-template-columns:minmax(0,1fr); gap:16px; align-items:start; }
.chart-card { min-width:0; overflow:hidden; }
.chart-heading { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:20px 22px 14px; }
.chart-heading h2 { margin:0; font-size:15px; }
.chart-heading p { margin:5px 0 0; color:var(--muted); font-size:11px; }
.count-pill { padding:6px 10px; border-radius:100px; background:#f3f5fa; color:#65728a; font-size:11px; white-space:nowrap; }
.svg-scroll { overflow:auto; min-height:620px; max-height:86vh; border-top:1px solid #f0f2f6; }
.tree-chart { display:block; width:100%; min-width:760px; }
.tree-edge { fill:none; stroke:#c9d1df; stroke-width:1.5; stroke-linejoin:round; stroke-linecap:round; }
.tree-edge.chosen { stroke:#315fce; stroke-width:2.2; }
.tree-edge.elite { stroke:#e8a63b; stroke-width:2.6; }
.tree-edge.failed { stroke:#e3b3b3; stroke-dasharray:4 4; }
.tree-edge.compact { stroke-width:1.5; }
.best-halo { fill:#e8a63b; opacity:.16; }
.best-star { fill:#c98a12; font:700 13px/1 system-ui; }
.node-score.best { fill:#946407; font-weight:700; }
.best-pill { flex:none; padding:3px 9px; border:1px solid #e8c98b; border-radius:100px; background:#fffaf0; color:#946407; font-size:10px; font-weight:600; white-space:nowrap; }
.iteration-table tr.best-row { background:#fffaf0; }
.iteration-table tr.best-row td { box-shadow:inset 0 -1px 0 #f3e2bd; }
.best-badge { margin-left:6px; padding:1px 6px; border-radius:100px; background:#e8a63b; color:#fff; font-size:9px; font-weight:700; white-space:nowrap; }
.node-score.failed { fill:#c14242; }
.zoom-controls .button.selected { border-color:var(--accent); background:#eef3ff; color:var(--accent); }
.node-tip { position:fixed; z-index:200; min-width:190px; max-width:320px; padding:9px 11px; border-radius:9px; background:#101828f2; box-shadow:0 8px 24px #10182840; color:#e8edf7; pointer-events:none; }
.code-drawer { position:fixed; top:0; right:0; z-index:320; display:flex; flex-direction:column; gap:10px; width:min(660px, 64vw); height:100vh; padding:18px 20px; background:var(--surface); border-left:1px solid var(--line); box-shadow:-18px 0 46px #10182826; }
.drawer-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.drawer-head-actions { display:flex; flex:none; align-items:center; gap:8px; }
.drawer-copy { white-space:nowrap; }
.drawer-copy.done { border-color:#178454; color:#178454; }
.drawer-copy.fail { border-color:#c14242; color:#c14242; }
.drawer-head h3 { margin:4px 0 0; font-size:14px; display:flex; align-items:center; gap:8px; }
.drawer-meta { margin:5px 0 0; color:var(--muted); font-size:10px; }
.drawer-hint { margin:0; color:var(--muted); font-size:11px; }
.drawer-facts { display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:8px 12px; margin:0; padding:10px 12px; border:1px solid var(--line); border-radius:10px; background:var(--accent-soft); }
.drawer-facts div { display:flex; flex-direction:column; gap:2px; }
.drawer-facts dt { color:var(--muted); font-size:10px; }
.drawer-facts dd { margin:0; font-size:12px; font-weight:600; }
.drawer-facts dd.fact-bad { color:#c14242; }
.drawer-metrics { display:flex; flex-wrap:wrap; gap:6px; }
.drawer-metrics span { display:flex; flex-direction:column; gap:1px; padding:5px 8px; border:1px solid var(--line); border-radius:7px; background:var(--surface); }
.drawer-metrics small { color:var(--muted); font-size:9px; }
.drawer-metrics b { font-size:10px; }
.drawer-code { flex:1; min-height:0; max-height:none; }
.drawer-foot { display:flex; gap:8px; }
.code-view { display:flex; overflow:auto; border-radius:10px; background:#172033; }
.line-numbers { flex:0 0 46px; padding:12px 8px; border-right:1px solid #26324a; color:#5a6b8a; font:11px/1.7 "IBM Plex Mono",monospace; text-align:right; user-select:none; }
.line-numbers span { display:block; }
.code-body { flex:1; margin:0; padding:12px 14px; color:#d7e0f1; font:11px/1.7 "IBM Plex Mono",monospace; white-space:pre; }
.code-line { display:block; }
.tok-comment { color:#7f8ea8; font-style:italic; }
.tok-string { color:#8fd6a0; }
.tok-keyword { color:#7fa9ff; font-weight:700; }
.tok-number { color:#e8b46b; }
.tip-row { display:flex; align-items:baseline; justify-content:space-between; gap:12px; font-size:10px; line-height:1.7; }
.tip-row small { color:#9fb0cc; }
.tip-row b { font-weight:600; overflow-wrap:anywhere; }
.generation-guide { stroke:#e9edf3; stroke-dasharray:3 5; }
.axis-label { fill:#8792a5; font-size:11px; }
.tree-node { cursor:pointer; }
.tree-node circle { stroke:#fff; stroke-width:2.5; transition:r .15s, filter .15s; }
.tree-node:hover circle, .tree-node.chosen circle { r:15; stroke:#263b79; }
.tree-node.highlighted circle { stroke:#e59e2d; stroke-width:3; }
.node-label { fill:#68748a; font-size:10px; pointer-events:none; }
.node-badge { fill:#fff; font-size:10px; font-weight:700; pointer-events:none; }
.node-title { fill:#3a4658; font-size:10px; font-weight:600; pointer-events:none; }
.node-score { fill:#178454; font-size:10px; font-weight:700; font-family:"IBM Plex Mono",monospace; pointer-events:none; }
.node-score.compact { fill:#54627a; font-size:8.5px; }
.table-scroll { max-height:420px; overflow:auto; margin-top:12px; }
.iteration-table { width:100%; border-collapse:collapse; font-size:11px; }
.iteration-table th { position:sticky; top:0; padding:7px 8px; border-bottom:1px solid var(--line); background:#f8f9fc; color:#78849a; font-size:10px; text-align:left; }
.iteration-table td { padding:7px 8px; border-bottom:1px solid #f0f2f6; }
.iteration-table tbody tr:hover { background:#fafbfe; }
.iteration-table a { color:var(--accent); text-decoration:none; }
.iteration-table a:hover { text-decoration:underline; }
.metrics-cell { max-width:280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.iteration-state { padding:3px 7px; border-radius:100px; background:#eef1f5; color:#68748a; font-size:9px; font-weight:700; }
.iteration-state.completed { background:#e7f7ef; color:#178454; }
.iteration-state.failed { background:#fff0f0; color:#c14242; }
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
.trend-chart { display:block; width:100%; min-width:720px; height:auto; max-height:min(78vh,900px); cursor:grab; }
.chart-heading-right { display:flex; align-items:center; gap:8px; }
.trend-group-title { fill:#5b6a80; font-size:12px; font-weight:700; }
.trend-na rect { fill:#f4f6fa; stroke:#e3e8f0; }
.trend-na text { fill:#8b96a8; font-size:10px; }
.chart-axis { stroke:#bdc7d6; }
.trend-edge { stroke:#cbd3e0; stroke-width:1; }
.trend-point { cursor:pointer; }
.trend-point circle { stroke:#fff; stroke-width:2; }
.trend-point.highlighted circle, .trend-point.chosen circle { stroke:#e5a02c; stroke-width:3; }
.selected-caption { padding:0 22px 14px; color:var(--muted); font-size:12px; }
.zoom-controls { display:flex; flex-wrap:wrap; align-items:center; justify-content:flex-end; gap:6px; }
.zoom-button { flex:none; min-height:30px; padding:4px 9px; font-size:11px; white-space:nowrap; }
.count-pill, .best-pill { flex:none; white-space:nowrap; }
.tree-chart { cursor:grab; }
.tree-chart:active { cursor:grabbing; }
.checkpoint-control .select-input { width:auto; min-width:180px; min-height:34px; padding:6px 10px; font-size:12px; }
.map-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; padding:4px 22px 18px; }
.map-card { border:1px solid var(--line); border-radius:10px; padding:12px; background:#fafbfe; }
.map-card header { display:flex; align-items:baseline; justify-content:space-between; gap:8px; margin-bottom:10px; }
.map-card header strong { font-size:12px; }
.map-card header span { color:var(--muted); font-size:10px; }
.map-svg { display:block; width:100%; height:auto; border:1px solid #e6eaf2; border-radius:6px; background:#fff; }
.map-cell { cursor:pointer; }
.map-cell:hover { stroke:#263b79; stroke-width:1.5; }
.map-cell.chosen { stroke:#e5a02c; stroke-width:2; }
.map-hint { margin:8px 0 0; color:var(--muted); font-size:10px; }
.empty-state { padding:26px 22px; color:var(--muted); font-size:12px; text-align:center; }
.empty-state strong { color:#36445c; font-size:13px; }
.empty-hint { max-width:560px; margin:8px auto 0; line-height:1.7; }
.empty-actions { display:flex; justify-content:center; gap:10px; margin-top:16px; }
.list-card { overflow:hidden; }
.list-toolbar { display:flex; gap:10px; padding:16px; border-bottom:1px solid var(--line); }
.list-toolbar .text-input { max-width:360px; }
.list-toolbar .select-input { width:auto; min-width:140px; }
.summary-row { display:grid; grid-template-columns:repeat(4,1fr); border-bottom:1px solid var(--line); }
.summary-row > div { display:flex; flex-direction:column; gap:5px; padding:14px 18px; border-right:1px solid var(--line); }
.summary-row > div:last-child { border:0; }
.summary-row small { color:var(--muted); font-size:10px; }
.summary-row strong { font-size:15px; }
.program-list { max-height:70vh; overflow:auto; }
.program-row { display:flex; align-items:flex-start; gap:16px; padding:14px 18px; border-bottom:1px solid #eef1f6; background:#fff; cursor:pointer; }
.program-row:hover, .program-row.active { background:#f5f7fc; }
.program-row.highlighted { box-shadow:inset 3px 0 0 #e59e2d; }
.program-row.elite { background:#fffaf0; box-shadow:inset 3px 0 0 #e8a63b; }
.program-row.elite:hover, .program-row.elite.active { background:#fdf3dd; }
.elite-badge { padding:2px 7px; border-radius:100px; background:#f7e2b0; color:#7a5206; font-size:9px; font-weight:700; white-space:nowrap; }
.program-rank { min-width:34px; padding-top:2px; color:#8792a5; font:700 13px "IBM Plex Mono",monospace; }
.program-row.highlighted .program-rank { color:#a46a0b; }
.program-main { display:flex; min-width:220px; flex:1; flex-direction:column; gap:8px; }
.score-line { display:flex; align-items:center; gap:8px; }
.score-name { color:var(--muted); font-size:11px; }
.score-line b { color:#263b79; font-size:13px; }
.score-bar { overflow:hidden; flex:1; max-width:220px; height:10px; border-radius:100px; background:#e9edf4; }
.score-bar i { display:block; height:100%; border-radius:100px; }
.program-facts { display:flex; flex-wrap:wrap; gap:4px 26px; margin:0; }
.program-facts > div { display:flex; gap:8px; min-width:0; }
.program-facts dt { color:var(--muted); font-size:11px; }
.program-facts dd { margin:0; overflow-wrap:anywhere; font-size:11px; }
.parent-link { border:0; padding:0; background:none; color:#315fce; cursor:pointer; font:inherit; text-decoration:underline; overflow-wrap:anywhere; }
.metric-bars { display:grid; min-width:260px; flex:0 0 320px; gap:4px; }
.metric-bar { display:grid; grid-template-columns:minmax(110px,1fr) 52px 100px; align-items:center; gap:2px 8px; }
.metric-scale { display:flex; grid-column:3; grid-row:2; justify-content:space-between; color:#9aa4b6; font-size:9px; font-style:normal; }
.metric-scale em { font-style:normal; }
.metric-name { overflow:hidden; color:#58667d; font-size:10px; text-overflow:ellipsis; white-space:nowrap; text-align:right; }
.metric-value { color:#263b79; font-size:10px; text-align:right; }
.metric-track { overflow:hidden; height:8px; border-radius:100px; background:#e9edf4; }
.metric-track i { display:block; height:100%; border-radius:100px; }
.open-window { flex:none; min-height:28px; padding:4px 9px; font-size:10px; }
@media(max-width:960px) { .program-row { flex-wrap:wrap; } .metric-bars { flex:1 1 100%; min-width:0; } .open-window { margin-left:auto; } }
.summary-bar { overflow:hidden; display:block; height:8px; margin-top:2px; border-radius:100px; background:#e9edf4; }
.summary-bar i { display:block; height:100%; border-radius:100px; background:#315fce; }
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
