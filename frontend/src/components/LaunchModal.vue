<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { api } from "../api";

const emit = defineEmits(["close", "started"]);

const form = ref({ template: "", task_name: "", prompt: "", initial_program: "", evaluator: "", objective_yaml: "", validation_yaml: "", iterations: 10 });
const templates = ref([]);
const templateDescription = ref("");
const runs = ref([]);
const resumeRun = ref("");
const resumeCheckpoints = ref([]);
const resumeCheckpoint = ref("");
const loading = ref(true);
const switching = ref(false);
const submitting = ref(false);
const error = ref("");

// Ant Design 下拉框的选项
const templateOptions = computed(() => templates.value.map((item) => ({ label: item.name, value: item.id })));
const runOptions = computed(() => [
  { label: "新建任务（不续跑）", value: "" },
  ...runs.value.map((run) => ({ label: run.name || run.id, value: run.id })),
]);
const resumeCheckpointOptions = computed(() => [
  { label: "最新检查点", value: "" },
  ...resumeCheckpoints.value.map((item) => ({ label: `${item.name} · ${item.programs} 程序`, value: item.name })),
]);

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

async function loadRuns() {
  try {
    runs.value = (await api.runs()).runs || [];
  } catch {
    runs.value = [];
  }
}

watch(resumeRun, async (runId) => {
  resumeCheckpoint.value = "";
  resumeCheckpoints.value = runId ? (await api.runCheckpoints(runId)).checkpoints || [] : [];
});

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
      // 不续跑就不传该字段（传空串会被后端当成非法值）
      resume_from: resumeRun.value
        ? resumeCheckpoint.value
          ? `${resumeRun.value}/${resumeCheckpoint.value}`
          : resumeRun.value
        : null,
      iterations: Number(form.value.iterations),
    });
    emit("started", response.run);
  } catch (cause) {
    error.value = cause.message;
  } finally {
    submitting.value = false;
  }
}

onMounted(async () => {
  try {
    applyDefaults(await api.launchDefaults());
    await loadRuns();
  } catch (cause) {
    error.value = cause.message;
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-card" role="dialog" aria-modal="true" aria-label="新建进化任务">
      <header class="modal-head">
        <div><p class="eyebrow">New Evolution</p><h2>新建进化任务</h2></div>
        <button class="icon-button" aria-label="关闭" @click="emit('close')">×</button>
      </header>

      <p class="message modal-warning">任务会在 Flask 所在机器执行本地 Python 代码。请仅提交可信的初始程序和评测脚本。</p>
      <p v-if="error" class="message error">{{ error }}</p>

      <div v-if="loading" class="empty-state">正在读取默认内容…</div>
      <form v-else class="launch-form" @submit.prevent="submit">
        <div class="template-row">
          <div class="form-field">
            <label class="field-label" for="template">任务模板</label>
            <p class="field-hint">模板提供默认输入、评分方式与候选校验；切换模板会覆盖下方内容。</p>
            <a-select
              id="template"
              v-model:value="form.template"
              class="text-input"
              :options="templateOptions"
              :disabled="switching"
              :dropdown-match-select-width="false"
              popup-class-name="oe-select-popup"
              @change="switchTemplate"
            />
            <p v-if="templateDescription" class="field-hint">{{ templateDescription }}</p>
          </div>
          <div class="form-field">
            <label class="field-label" for="task-name">任务名称</label>
            <p class="field-hint">留空则使用模板名称，仅用于任务列表展示。</p>
            <input id="task-name" v-model="form.task_name" class="text-input" type="text" maxlength="80" placeholder="使用模板名称" />
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
        <div class="form-field">
          <label class="field-label" for="resume-run">从已有任务续跑</label>
          <p class="field-hint">可选。选定任务后默认从其最新检查点继续；也可指定某个检查点。</p>
          <div class="resume-row">
            <a-select
              id="resume-run"
              v-model:value="resumeRun"
              class="text-input"
              :options="runOptions"
              :dropdown-match-select-width="false"
              popup-class-name="oe-select-popup"
            />
            <a-select
              v-if="resumeRun"
              v-model:value="resumeCheckpoint"
              class="text-input"
              :options="resumeCheckpointOptions"
              :dropdown-match-select-width="false"
              popup-class-name="oe-select-popup"
            />
          </div>
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
          <button class="button primary" :disabled="submitting">{{ submitting ? "正在启动…" : "开始进化" }}</button>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay { position:fixed; z-index:60; inset:0; display:flex; align-items:flex-start; justify-content:center; padding:32px 16px; overflow:auto; background:rgba(23,32,51,.45); backdrop-filter:blur(2px); }
.modal-card { width:min(860px,100%); max-height:calc(100vh - 64px); display:flex; flex-direction:column; overflow:auto; padding:22px; border-radius:16px; background:var(--surface,#fff); box-shadow:0 24px 70px rgba(23,32,51,.28); }
.modal-head { display:flex; align-items:center; justify-content:space-between; gap:14px; margin-bottom:14px; }
.modal-head h2 { margin:2px 0 0; font-size:20px; }
.icon-button { width:32px; height:32px; border:0; border-radius:8px; background:#f2f4f8; color:#68748a; font-size:20px; }
.modal-warning { margin:0 0 12px; }
.launch-form { display:grid; gap:15px; }
.form-field { min-width:0; }
.form-field .code-input { min-height:150px; }
.form-field .code-input.small { min-height:100px; }
.template-row { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; }
.resume-row { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }
.advanced { padding:13px 15px; border:1px solid var(--line,#e2e6ee); border-radius:10px; }
.advanced summary { cursor:pointer; font-size:13px; font-weight:600; }
.advanced .form-field { margin-top:11px; }
.launch-actions { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; }
.iteration-field { width:min(220px,100%); }
.iteration-field .field-label { margin-bottom:7px; }
.empty-state { padding:40px 0; color:var(--muted,#68748a); font-size:13px; text-align:center; }
@media(max-width:600px) { .modal-card { padding:15px; } .launch-actions { align-items:stretch; flex-direction:column; } .iteration-field { width:100%; } }
</style>
