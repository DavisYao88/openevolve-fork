<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { FileTextOutlined, FolderOpenOutlined, FolderOutlined } from "@ant-design/icons-vue";
import { api } from "../api";
import CodePreview from "../components/CodePreview.vue";

const route = useRoute();
const runId = computed(() => route.query.run_id || "");

const tree = ref([]);
const runDir = ref("");
const loading = ref(true);
const error = ref("");
const expandedKeys = ref([]);
const selectedKeys = ref([]);
const selectedPath = ref("");
const file = ref(null);
const fileError = ref("");
const fileLoading = ref(false);
const keyword = ref("");

function toTreeData(nodes) {
  return (nodes || []).map((node) => ({
    key: node.path,
    name: node.name,
    type: node.type,
    size: node.size,
    isLeaf: node.type === "file",
    selectable: node.type === "file",
    children: node.type === "dir" ? toTreeData(node.children) : undefined,
  }));
}

const treeData = computed(() => toTreeData(tree.value));

function countFiles(nodes) {
  return (nodes || []).reduce((total, node) => (
    node.type === "file" ? total + 1 : total + countFiles(node.children)
  ), 0);
}

const fileCount = computed(() => countFiles(tree.value));

const isJson = computed(() => /\.json$/i.test(selectedPath.value));

const displayContent = computed(() => {
  const raw = file.value?.content || "";
  if (isJson.value) {
    try {
      return JSON.stringify(JSON.parse(raw), null, 2);
    } catch {
      return raw;
    }
  }
  return raw;
});



const BEST_PROGRAM_PATH = "output/best/best_program.py";

function isBestPath(path) {
  return path === BEST_PROGRAM_PATH;
}

function sizeLabel(size) {
  if (!Number.isFinite(size)) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

async function openFile(path) {
  selectedPath.value = path;
  selectedKeys.value = [path];
  fileLoading.value = true;
  fileError.value = "";
  file.value = null;
  try {
    file.value = await api.runFile(runId.value, path);
  } catch (cause) {
    fileError.value = String(cause.message || cause);
  } finally {
    fileLoading.value = false;
  }
}

function onSelect(keys) {
  if (keys.length) openFile(keys[0]);
}

function onExpand(keys) {
  expandedKeys.value = keys;
}


function firstFileOf(sectionName) {
  const stack = [...(tree.value.find((section) => section.name === sectionName)?.children || [])];
  while (stack.length) {
    const node = stack.shift();
    if (node.type === "file") return node.path;
    stack.unshift(...(node.children || []));
  }
  return "";
}

// 把目标文件的所有上级目录展开，便于从其它页面直接定位到某个文件
function expandTo(path) {
  const parts = String(path || "").split("/").filter(Boolean);
  const keys = new Set(expandedKeys.value);
  for (let index = 1; index < parts.length; index += 1) {
    keys.add(parts.slice(0, index).join("/"));
  }
  expandedKeys.value = [...keys];
}

async function loadTree() {
  loading.value = true;
  error.value = "";
  try {
    const response = await api.runFiles(runId.value);
    tree.value = response.tree || [];
    runDir.value = response.run_dir || "";
    expandedKeys.value = tree.value.map((section) => section.path);
    const requested = String(route.query.path || "");
    const preferred = requested || firstFileOf("input") || firstFileOf("output");
    if (preferred) {
      expandTo(preferred);
      await openFile(preferred);
    }
  } catch (cause) {
    error.value = String(cause.message || cause);
  } finally {
    loading.value = false;
  }
}

onMounted(loadTree);
watch(runId, loadTree);
</script>

<template>
  <section>
    <div class="page-heading">
      <div>
        <p class="eyebrow">Code Browser</p>
        <h1>任务代码浏览</h1>
        <p class="mono">{{ runDir || runId || "未指定任务" }}</p>
      </div>
      <div class="heading-actions">
        <RouterLink v-if="runId" class="button" :to="{ path: `/runs/${runId}` }">任务详情</RouterLink>
        <RouterLink class="button" :to="{ path: '/', query: route.query }">可视化</RouterLink>
        <RouterLink class="button" to="/runs">任务列表</RouterLink>
      </div>
    </div>

    <p v-if="error" class="message error">{{ error }}</p>
    <div v-else-if="loading" class="card empty-state">正在读取任务文件…</div>
    <div v-else-if="!tree.length" class="card empty-state">该任务还没有 input / output 文件。</div>

    <div v-else class="code-layout">
      <aside class="card tree-pane">
        <header class="tree-head">
          <h2>目录</h2>
          <span class="count-pill">{{ fileCount }} 个文件</span>
        </header>
        <a-input v-model:value="keyword" class="tree-search" placeholder="搜索文件名" allow-clear />
        <div class="tree-scroll">
          <a-tree
            :tree-data="treeData"
            :expanded-keys="expandedKeys"
            :selected-keys="selectedKeys"
            :show-icon="false"
            show-line
            block-node
            @expand="onExpand"
            @select="onSelect"
          >
            <template #title="node">
              <span class="tree-node">
                <FolderOpenOutlined v-if="node.type === 'dir' && expandedKeys.includes(node.key)" class="node-icon dir" />
                <FolderOutlined v-else-if="node.type === 'dir'" class="node-icon dir" />
                <FileTextOutlined v-else class="node-icon file" />
                <span class="tree-name mono">{{ node.name }}</span>
                <span v-if="isBestPath(node.key)" class="tree-best">★</span>
                <span v-if="node.type === 'file'" class="tree-size">{{ sizeLabel(node.size) }}</span>
              </span>
            </template>
          </a-tree>
        </div>
      </aside>

      <section class="card file-pane">
        <header class="file-head">
          <div>
            <h2 class="mono">{{ file?.name || selectedPath || "未选择文件" }}</h2>
            <p v-if="file" class="file-meta mono">
              {{ file.path }} · {{ sizeLabel(file.size) }}
              <span v-if="file.truncated" class="truncated">（超过 300 KB，已截断显示）</span>
              <span v-if="isBestPath(file.path)" class="best-badge">★ 最优输出程序</span>
              <span v-if="isJson" class="formatted">已格式化 JSON</span>
            </p>
          </div>
          <div class="file-actions"></div>
        </header>

        <p v-if="fileLoading" class="empty-hint">正在读取…</p>
        <p v-else-if="fileError" class="message error">{{ fileError }}</p>
        <p v-else-if="!file" class="empty-hint">从左侧点击任意文件查看内容。</p>
        <CodePreview
          v-else
          :content="displayContent"
          :filename="selectedPath"
          copy-label="复制内容"
          min-height="480px"
        />
      </section>
    </div>
  </section>
</template>

<style scoped>
.heading-actions { display:flex; gap:8px; }
.code-layout { display:grid; grid-template-columns:330px minmax(0,1fr); gap:14px; align-items:start; }
.tree-pane { display:flex; flex-direction:column; padding:14px; max-height:calc(100vh - 150px); }
.tree-head { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:10px; }
.tree-head h2 { margin:0; font-size:13px; }
.tree-search { margin-bottom:8px; }
.tree-scroll { flex:1; overflow:auto; }
.tree-node { display:flex; align-items:center; gap:6px; font-size:11px; }
.node-icon.dir { color:#315fce; }
.node-icon.file { color:#8792a5; }
.tree-name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tree-size { flex:none; color:var(--muted); font-size:9px; }
.file-pane { padding:16px 18px; }
.file-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:12px; }
.file-head h2 { margin:0; font-size:14px; overflow-wrap:anywhere; }
.file-meta { margin:4px 0 0; color:var(--muted); font-size:10px; overflow-wrap:anywhere; }
.tree-best { flex:none; color:#c98a12; font-size:11px; }
.best-badge { margin-left:6px; padding:1px 7px; border-radius:100px; background:#e8a63b; color:#fff; font-size:9px; font-weight:700; }
.truncated { color:#946407; }
.formatted { margin-left:6px; padding:1px 6px; border-radius:100px; background:#eef3ff; color:var(--accent); }
.empty-hint { margin:0; padding:8px 0; color:var(--muted); font-size:11px; }
@media (max-width: 900px) {
  .code-layout { grid-template-columns:1fr; }
}
</style>
