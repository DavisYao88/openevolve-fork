<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { api } from "../api";

const form = ref({ template: "", task_name: "", prompt: "", initial_program: "", evaluator: "", objective_yaml: "", validation_yaml: "", iterations: 10 });
const templates = ref([]);
const templateDescription = ref("");
const currentRun = ref(null);
const loading = ref(true);
const switching = ref(false);
const submitting = ref(false);
const error = ref("");
let timer;

async function refreshStatus() {
  try {
    const response = await api.currentRun();
    currentRun.value = response.run;
    if (response.run?.status === "running" && !timer) {
      timer = window.setInterval(refreshStatus, 2000);
    } else if (response.run?.status !== "running" && timer) {
      window.clearInterval(timer);
      timer = null;
    }
  } catch (cause) {
    error.value = cause.message;
  }
}

function applyDefaults(defaults) {
  templates.value = defaults.templates || [];
  form.value.template = defaults.template.id;
  form.value.task_name = "";
  form.value.prompt = defaults.prompt;
  form.value.initial_program = defaults.initial_program;
  form.value.evaluator = defaults.evaluator;
  form.value.objective_yaml = defaults.objective_yaml || "";
  form.value.validation_yaml = defaults.validation_yaml || "";
  form.value.iterations = defaults.iterations;
  templateDescription.value = defaults.template.description || "";
}

async function switchTemplate() {
  switching.value = true;
  error.value = "";
  try {
    applyDefaults(await api.launchDefaults(form.value.template));
  } catch (cause) {
    error.value = cause.message;
  } finally {
    switching.value = false;
  }
}

async function submit() {
  submitting.value = true;
  error.value = "";
  try {
    const response = await api.startRun({
      template: form.value.template,
      task_name: form.value.task_name,
      prompt: form.value.prompt,
      initial_program: form.value.initial_program,
      evaluator: form.value.evaluator,
      objective: form.value.objective_yaml,
      validation: form.value.validation_yaml,
      iterations: Number(form.value.iterations),
    });
    currentRun.value = response.run;
    if (!timer) timer = window.setInterval(refreshStatus, 2000);
  } catch (cause) {
    error.value = cause.message;
  } finally {
    submitting.value = false;
  }
}

onMounted(async () => {
  try {
    applyDefaults(await api.launchDefaults());
    await refreshStatus();
  } catch (cause) {
    error.value = cause.message;
  } finally {
    loading.value = false;
  }
});
onUnmounted(() => { if (timer) window.clearInterval(timer); });
</script>

<template>
  <section>
    <div class="page-heading"><div><p class="eyebrow">New Evolution</p><h1>新建进化任务</h1><p>选择任务模板，填写任务提示词、初始程序、评测脚本和进化轮数。</p></div></div>
    <p class="message">任务会在 Flask 所在机器执行本地 Python 代码。请仅提交可信的初始程序和评测脚本。</p>
    <div v-if="loading" class="card empty-state">正在读取默认内容…</div>
    <form v-else class="launch-form card" @submit.prevent="submit">
      <div class="template-row">
        <div class="form-field">
          <label class="field-label" for="template">任务模板</label>
          <p class="field-hint">模板提供默认输入、评分方式与候选校验；切换模板会覆盖下方内容。</p>
          <select id="template" v-model="form.template" class="text-input" :disabled="switching" @change="switchTemplate">
            <option v-for="item in templates" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
          <p v-if="templateDescription" class="field-hint">{{ templateDescription }}</p>
        </div>
        <div class="form-field">
          <label class="field-label" for="task-name">任务名称</label>
          <p class="field-hint">留空则使用模板名称，仅用于任务列表展示。</p>
          <input id="task-name" v-model="form.task_name" class="text-input" type="text" maxlength="80" placeholder="使用模板名称"/>
        </div>
      </div>
      <div class="form-field">
        <label class="field-label" for="prompt">任务提示词（Markdown）</label>
        <p class="field-hint">说明优化目标、约束和评分方式。</p>
        <textarea id="prompt" v-model="form.prompt" class="code-input" required></textarea>
      </div>
      <div class="form-field">
        <label class="field-label" for="initial-program">初始程序代码</label>
        <p class="field-hint">作为进化种子；任务启动时会复制到独立任务目录。</p>
        <textarea id="initial-program" v-model="form.initial_program" class="code-input" required></textarea>
      </div>
      <div class="form-field">
        <label class="field-label" for="evaluator">评测脚本代码</label>
        <p class="field-hint">接收候选程序路径，跑真实评测，并向标准输出打印一个指标 JSON；启动时自动配置为 runner_command。</p>
        <textarea id="evaluator" v-model="form.evaluator" class="code-input" required></textarea>
      </div>
      <details class="advanced">
        <summary>评分与校验配置（YAML，可留空沿用模板）</summary>
        <div class="form-field">
          <label class="field-label" for="objective">objective：怎么算分</label>
          <p class="field-hint">把评测脚本返回的指标折算成 combined_score（越大越好）。</p>
          <textarea id="objective" v-model="form.objective_yaml" class="code-input small"></textarea>
        </div>
        <div class="form-field">
          <label class="field-label" for="validation">validation：允许改什么</label>
          <p class="field-hint">params_dict 只允许改参数字典的值，none 不做结构校验。</p>
          <textarea id="validation" v-model="form.validation_yaml" class="code-input small"></textarea>
        </div>
      </details>
      <div class="launch-actions">
        <label class="iteration-field"><span class="field-label">进化轮数</span><input v-model.number="form.iterations" class="text-input" type="number" min="1" required></label>
        <button class="button primary" :disabled="submitting || currentRun?.status === 'running'">{{ submitting ? "正在启动…" : currentRun?.status === "running" ? "已有任务运行中" : "开始进化" }}</button>
      </div>
    </form>

    <div v-if="error" class="message error launch-error">{{ error }}</div>
    <section v-if="currentRun" class="card run-status">
      <div class="run-status-heading"><div><p class="eyebrow">Run Status</p><h2>任务运行状态</h2></div><span class="status-badge" :class="currentRun.status">{{ currentRun.status === "running" ? "运行中" : currentRun.status === "completed" ? "已完成" : "失败" }}</span></div>
      <p class="path-line"><strong>任务目录</strong><span class="mono">{{ currentRun.run_dir }}</span></p>
      <p class="path-line"><strong>输出目录</strong><span class="mono">{{ currentRun.output_dir }}</span></p>
      <pre class="log-view">{{ currentRun.log || "等待日志…" }}</pre>
      <RouterLink v-if="currentRun.id" class="button" :to="{ path: '/', query: { run_id: currentRun.id } }">查看进化结果 →</RouterLink>
    </section>
  </section>
</template>

<style scoped>
.launch-form { display:grid; gap:17px; margin-top:16px; padding:20px; }
.form-field { min-width:0; }
.form-field .code-input { min-height:190px; }
.form-field .code-input.small { min-height:120px; }
.template-row { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:16px; }
.advanced { padding:14px 16px; border:1px solid var(--line,#e2e6ee); border-radius:10px; }
.advanced summary { cursor:pointer; font-size:13px; font-weight:600; }
.advanced .form-field { margin-top:12px; }
.launch-actions { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; }
.iteration-field { width:min(220px,100%); }
.iteration-field .field-label { margin-bottom:7px; }
.launch-error { margin-top:14px; }
.run-status { margin-top:20px; padding:20px; }
.run-status-heading { display:flex; align-items:center; justify-content:space-between; }
.run-status-heading .eyebrow { margin:0 0 4px; }.run-status-heading h2 { margin:0; font-size:17px; }
.status-badge { padding:5px 9px; border-radius:100px; background:#eef1f5; color:#68748a; font-size:10px; font-weight:700; }
.status-badge.running { background:#e8efff; color:#315fce; }.status-badge.completed { background:#e7f7ef; color:#178454; }.status-badge.failed { background:#fff0f0; color:#c14242; }
.path-line { display:grid; grid-template-columns:88px minmax(0,1fr); gap:10px; color:var(--muted); font-size:11px; }
.path-line span { overflow-wrap:anywhere; color:#36445c; }
.log-view { max-height:360px; overflow:auto; padding:13px; border-radius:9px; background:#172033; color:#d7e0f1; font:10px/1.55 "IBM Plex Mono",monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
@media(max-width:600px) { .launch-form { padding:14px; }.launch-actions { align-items:stretch; flex-direction:column; }.iteration-field { width:100%; } }
</style>
