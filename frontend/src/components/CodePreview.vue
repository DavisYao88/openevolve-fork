<script setup>
// 统一的代码预览：行号 + 语法高亮 + 右上角悬浮复制按钮。
// 演化树代码抽屉、任务代码浏览页、输入文件预览弹窗都用它，避免各处样式和交互不一致。
import { computed, onUnmounted, ref } from "vue";
import { escapeHtml, highlightJsonLine, highlightLine } from "../highlight";

const props = defineProps({
  content: { type: String, default: "" },
  filename: { type: String, default: "" },
  // 内容类型已知时直接指定（例如演化程序存在 .json 记录里，但内容是 Python）
  language: { type: String, default: "" },
  copyLabel: { type: String, default: "复制代码" },
  maxHeight: { type: String, default: "70vh" },
  minHeight: { type: String, default: "0" },
});

const language = computed(() => {
  if (props.language) return props.language;
  if (/\.json$/i.test(props.filename)) return "json";
  if (/\.py$/i.test(props.filename)) return "python";
  if (/\.ya?ml$/i.test(props.filename)) return "yaml";
  return "text";
});

const isJson = computed(() => language.value === "json");
const isPython = computed(() => language.value === "python" || language.value === "yaml");

const displayContent = computed(() => {
  const raw = props.content || "";
  if (isJson.value) {
    try {
      return JSON.stringify(JSON.parse(raw), null, 2);
    } catch {
      return raw;
    }
  }
  return raw;
});

const lines = computed(() => String(displayContent.value).split("\n"));

function renderLine(line) {
  if (isJson.value) return highlightJsonLine(line);
  if (isPython.value) return highlightLine(line);
  return escapeHtml(line);
}

const copyState = ref("");
let copyTimer;

async function copy() {
  const text = displayContent.value;
  if (!text) return;
  let ok = true;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    ok = false;
  }
  copyState.value = ok ? "done" : "fail";
  window.clearTimeout(copyTimer);
  copyTimer = window.setTimeout(() => {
    copyState.value = "";
  }, 1600);
}

onUnmounted(() => window.clearTimeout(copyTimer));
</script>

<template>
  <div class="code-shell">
    <button
      class="button code-copy-float"
      :class="copyState"
      :disabled="!displayContent"
      :title="`${copyLabel}到剪贴板`"
      @click="copy"
    >{{ copyState === "done" ? "已复制" : (copyState === "fail" ? "复制失败" : copyLabel) }}</button>
    <div class="code-view" :style="{ maxHeight, minHeight }">
      <div class="line-numbers">
        <span v-for="(_, index) in lines" :key="index">{{ index + 1 }}</span>
      </div>
      <pre class="code-body"><code><span v-for="(line, index) in lines" :key="index" class="code-line" v-html="renderLine(line)"></span></code></pre>
    </div>
  </div>
</template>

<style scoped>
.code-shell { position:relative; display:flex; flex:1; min-height:0; }
.code-view { display:flex; flex:1; min-height:0; overflow:auto; border-radius:10px; background:#172033; }
.line-numbers { flex:0 0 46px; padding:12px 8px; border-right:1px solid #26324a; color:#5a6b8a; font:11px/1.7 "IBM Plex Mono",monospace; text-align:right; user-select:none; }
.line-numbers span { display:block; }
.code-body { flex:1; margin:0; padding:12px 104px 12px 14px; color:#d7e0f1; font:11px/1.7 "IBM Plex Mono",monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
.code-line { display:block; }
.tok-comment { color:#6b7c9b; font-style:italic; }
.tok-keyword { color:#c792ea; }
.tok-string { color:#8fd6a0; }
.tok-number { color:#e8b46b; }
.tok-key { color:#7fa9ff; }
.tok-str { color:#8fd6a0; }
.tok-num { color:#e8b46b; }
.tok-lit { color:#c792ea; }
</style>
