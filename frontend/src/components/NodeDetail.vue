<script>
// 程序详情侧栏。原来写在 EvolutionView 里用字符串 template 定义，
// runtime-only 的 Vue 构建不支持运行时编译，面板一直是空的；改成正常的 SFC。
export default {
  props: { node: Object, nodes: Array, archive: Array, metric: String },
  emits: ["close", "open", "code"],
  data() {
    return { active: "概览" };
  },
  computed: {
    parent() {
      return this.nodes.find((item) => item.id === this.node.parent_id);
    },
    children() {
      return this.nodes.filter((item) => item.parent_id === this.node.id);
    },
    cloneNodes() {
      const base = String(this.node.id).split("-copy")[0];
      return this.nodes.filter(
        (item) => item.id !== this.node.id && String(item.id).split("-copy")[0] === base,
      );
    },
    tabs() {
      return [
        "概览",
        ...(this.node.code ? ["代码"] : []),
        ...(this.node.prompts && Object.keys(this.node.prompts).length ? ["提示词"] : []),
        ...(this.children.length ? ["子程序"] : []),
        ...(this.cloneNodes.length ? ["克隆"] : []),
        ...(this.parent?.code && this.node.code ? ["差异"] : []),
      ];
    },
    promptText() {
      return JSON.stringify(this.node.prompts || this.node.artifacts_json || {}, null, 2);
    },
    diffLines() {
      const parent = (this.parent?.code || "").split("\n");
      const current = (this.node.code || "").split("\n");
      const max = Math.max(parent.length, current.length);
      return Array.from({ length: max }, (_, index) => {
        if (parent[index] === current[index]) return { type: "same", text: current[index] || "" };
        return {
          type:
            parent[index] === undefined
              ? "added"
              : current[index] === undefined
                ? "removed"
                : "changed",
          text: current[index] ?? `− ${parent[index]}`,
        };
      });
    },
  },
};
</script>

<template>
  <div class="node-detail">
    <div class="detail-head">
      <div>
        <p class="eyebrow">Program Details</p>
        <h2>程序详情</h2>
      </div>
      <button class="icon-button" aria-label="关闭详情" @click="$emit('close')">×</button>
    </div>

    <div class="node-stats">
      <div><small>程序 ID</small><strong class="mono">{{ node.id }}</strong></div>
      <div><small>代数 / 岛屿</small><strong>{{ node.generation ?? "—" }} / {{ node.island ?? "—" }}</strong></div>
      <div>
        <small>{{ metric }}</small>
        <strong>{{ typeof node.metrics?.[metric] === "number" && Number.isFinite(node.metrics[metric]) ? node.metrics[metric].toFixed(4) : "—" }}</strong>
      </div>
      <div><small>父程序</small><strong class="mono">{{ node.parent_id || "无" }}</strong></div>
    </div>

    <div class="metric-tags">
      <span v-for="(value, name) in node.metrics || {}" :key="name"><small>{{ name }}</small><b>{{ value }}</b></span>
    </div>

    <div class="detail-tabs">
      <button v-for="tab in tabs" :key="tab" :class="{ active: active === tab }" @click="active = tab">{{ tab }}</button>
    </div>

    <div class="detail-body">
      <template v-if="active === '概览'">
        <p v-if="archive.includes(node.id)" class="archive-label">★ MAP-Elites 精英档案程序</p>
        <p>创建时间：{{ node.created_at || "未记录" }}</p>
        <p>子程序：{{ children.length }}　克隆：{{ cloneNodes.length }}</p>
        <div class="detail-actions">
          <button class="button primary" @click="$emit('code', node)">查看代码</button>
          <button class="button" @click="$emit('open', node)">在新窗口打开完整程序页</button>
        </div>
      </template>
      <pre v-else-if="active === '代码'" class="code-panel">{{ node.code }}</pre>
      <pre v-else-if="active === '提示词'" class="code-panel">{{ promptText }}</pre>
      <ul v-else-if="active === '子程序'">
        <li v-for="child in children" :key="child.id" class="mono">{{ child.id }} · 第 {{ child.generation }} 代</li>
      </ul>
      <ul v-else-if="active === '克隆'">
        <li v-for="clone in cloneNodes" :key="clone.id" class="mono">{{ clone.id }}</li>
      </ul>
      <div v-else class="diff-panel">
        <div v-for="(line, index) in diffLines" :key="index" :class="line.type">{{ line.text }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.node-detail { padding:18px; }
.detail-head { display:flex; justify-content:space-between; align-items:center; }
.detail-head .eyebrow { margin-bottom:3px; }
.detail-head h2 { margin:0; font-size:17px; }
.icon-button { width:30px; height:30px; border:0; border-radius:8px; background:#f2f4f8; color:#68748a; font-size:20px; cursor:pointer; }
.node-stats { display:grid; gap:10px; margin-top:17px; }
.node-stats > div { display:flex; flex-direction:column; gap:4px; min-width:0; }
.node-stats small, .metric-tags small { color:var(--muted); font-size:10px; }
.node-stats strong { overflow-wrap:anywhere; font-size:12px; }
.metric-tags { display:flex; flex-wrap:wrap; gap:6px; margin-top:13px; }
.metric-tags span { display:flex; flex-direction:column; gap:3px; padding:6px 8px; border:1px solid var(--line); border-radius:7px; }
.metric-tags b { font-size:11px; }
.detail-tabs { display:flex; flex-wrap:wrap; gap:5px; margin:16px 0 0; padding-top:12px; border-top:1px solid var(--line); }
.detail-tabs button { padding:6px 8px; border:0; border-radius:6px; background:#f4f6fa; color:#68748a; font-size:10px; cursor:pointer; }
.detail-tabs button.active { background:var(--accent-soft); color:var(--accent); font-weight:700; }
.detail-body { max-height:55vh; overflow:auto; padding-top:11px; color:#57647a; font-size:12px; }
.detail-body p { margin:8px 0; }
.detail-actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
.code-panel, .diff-panel { overflow:auto; max-height:48vh; margin:0; padding:12px; border-radius:8px; background:#172033; color:#d7e0f1; font:11px/1.55 "IBM Plex Mono",monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
.diff-panel div { padding:1px 5px; }
.diff-panel .added { background:#174d3b; color:#b4f0d4; }
.diff-panel .removed { background:#592d36; color:#ffd0d0; }
.diff-panel .changed { color:#ffe1a2; }
.archive-label { color:#b27810; font-weight:700; }
.detail-body ul { margin:8px 0; padding-left:16px; }
</style>
