<script setup>
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { api } from "../api";

const PROVIDERS = [
  { value: "deepseek", label: "DeepSeek", apiBase: "https://api.deepseek.com", model: "deepseek-chat" },
  { value: "openai", label: "OpenAI", apiBase: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  { value: "azure", label: "Azure OpenAI", apiBase: "https://<resource>.openai.azure.com", model: "gpt-4o" },
  { value: "custom", label: "自定义（OpenAI 兼容）", apiBase: "", model: "" },
];

const providerOptions = PROVIDERS.map((item) => ({ label: item.label, value: item.value }));

const theme = ref(localStorage.getItem("openevolve.theme") === "dark" ? "dark" : "light");
const provider = ref("deepseek");
const modelName = ref("");
const apiBase = ref("https://api.deepseek.com");
const apiKey = ref("");
const hasApiKey = ref(false);
const apiKeyMasked = ref("");
const apiKeySource = ref("");
const envFile = ref(false);
const temperature = ref("0.6");
const maxTokens = ref("16000");
const loading = ref(true);
const saving = ref(false);
const message = ref("");
const error = ref("");

// 各供应商各自的 Key（掩码），后端一次返回，切换供应商时直接查表
const providerKeyMasks = ref({});
const envKeyMasked = ref("");

const providerLabel = computed(
  () => PROVIDERS.find((item) => item.value === provider.value)?.label || provider.value
);

// 已存过 Key 的供应商，用于提示「各自独立、互不覆盖」
const savedProviders = computed(() =>
  Object.keys(providerKeyMasks.value)
    .map((value) => PROVIDERS.find((item) => item.value === value)?.label || value)
    .join("、")
);

// 切换到某个供应商时，刷新该供应商自己的 Key 状态（env 则全局生效）
function refreshKeyState() {
  if (apiKeySource.value === "env") {
    hasApiKey.value = true;
    apiKeyMasked.value = envKeyMasked.value;
    return;
  }
  const masked = providerKeyMasks.value[provider.value] || "";
  apiKeyMasked.value = masked;
  hasApiKey.value = !!masked;
}

const keyHint = computed(() => {
  if (apiKeySource.value === "env") {
    return `环境变量 / .env.local 里的 Key 对所有供应商生效：${envKeyMasked.value}`;
  }
  if (!hasApiKey.value) return `「${providerLabel.value}」尚未配置 API Key`;
  return `「${providerLabel.value}」已保存：${apiKeyMasked.value}（来自本地 secrets 文件）`;
});

const placeholder = computed(() => {
  if (apiKeySource.value === "env") return `环境变量已生效：${envKeyMasked.value}（留空不修改）`;
  if (hasApiKey.value) return `已保存：${apiKeyMasked.value}（留空不修改）`;
  return "粘贴 API Key";
});

// 说明 Key 存在哪、怎么注入（每个供应商各一把，互不覆盖）
const keyNote = computed(() => {
  const tail = "任务配置里只写 ${OPENEVOLVE_API_KEY} 占位符，运行时由子进程环境变量注入。";
  if (apiKeySource.value === "env") {
    return `当前生效的是环境变量 / .env.local（已 gitignore），优先级最高，对所有供应商都生效；${tail}`;
  }
  if (hasApiKey.value) {
    return `「${providerLabel.value}」的 Key 保存在本机 evolve_tasks/llm_secrets.json（600 权限、已 gitignore），接口只回传掩码；每个供应商各存一把，切换不会覆盖其他供应商；${tail}`;
  }
  return `还没有「${providerLabel.value}」的可用 Key：可以在本页保存，或在 .env.local 里设置 OPENEVOLVE_API_KEY。每个供应商的 Key 单独保存；${tail}`;
});

function applyTheme(value) {
  theme.value = value;
  document.documentElement.dataset.theme = value;
  localStorage.setItem("openevolve.theme", value);
  window.dispatchEvent(new Event("openevolve-theme"));
}

function pickProvider() {
  const preset = PROVIDERS.find((item) => item.value === provider.value);
  refreshKeyState();
  apiKey.value = "";
  if (!preset) return;
  if (preset.apiBase) apiBase.value = preset.apiBase;
  if (preset.model && !modelName.value) modelName.value = preset.model;
}

function applySettings(data) {
  const llm = data?.llm || {};
  provider.value = llm.provider || "deepseek";
  modelName.value = llm.primary_model || "";
  apiBase.value = llm.api_base || "";
  apiKeySource.value = llm.api_key_source || "";
  providerKeyMasks.value = llm.provider_keys || {};
  // 后端未返回 env 掩码时退回当前生效掩码（env 模式下二者等价）
  envKeyMasked.value =
    llm.env_api_key_masked || (llm.api_key_source === "env" ? llm.api_key_masked : "") || "";
  refreshKeyState();
  envFile.value = !!llm.env_file;
  temperature.value = llm.temperature ?? "0.6";
  maxTokens.value = llm.max_tokens ?? "16000";
  if (data?.theme === "dark" || data?.theme === "light") applyTheme(data.theme);
}

async function loadSettings() {
  loading.value = true;
  try {
    applySettings(await api.settings());
  } catch (cause) {
    error.value = `读取设置失败：${cause.message}`;
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  message.value = "";
  error.value = "";
  try {
    const payload = {
      provider: provider.value,
      api_base: apiBase.value.trim(),
      primary_model: modelName.value.trim(),
      temperature: temperature.value,
      max_tokens: maxTokens.value,
    };
    const typedKey = apiKey.value.trim();
    if (typedKey) payload.api_key = typedKey;
    const data = await api.saveSettings({ theme: theme.value, llm: payload });
    apiKey.value = "";
    applySettings(data);
    if (typedKey && apiKeySource.value === "env") {
      message.value =
        `已把 Key 存到「${providerLabel.value}」的槽位，但生效的仍是 .env.local / 环境变量里的 Key（优先级更高）。` +
        "要改用本页保存的 Key，请先删掉 .env.local 里的 OPENEVOLVE_API_KEY。";
    } else if (typedKey) {
      message.value = `已为「${providerLabel.value}」保存 Key，下一次启动演化任务时生效。`;
    } else {
      message.value = "已保存，下一次启动演化任务时生效。";
    }
  } catch (cause) {
    error.value = `保存失败：${cause.message}`;
  } finally {
    saving.value = false;
  }
}

onMounted(loadSettings);
</script>

<template>
  <section>
    <div class="page-heading">
      <div>
        <p class="eyebrow">Settings</p>
        <h1>设置</h1>
        <p>外观与模型供应商。模型配置会写入下一次启动的演化任务（<code>llm</code> 段）。</p>
      </div>
      <div class="heading-actions">
        <button class="button primary" :disabled="saving || loading" @click="save">{{ saving ? "保存中…" : "保存设置" }}</button>
        <RouterLink class="button" to="/runs">返回任务列表</RouterLink>
      </div>
    </div>

    <p v-if="error" class="message error">{{ error }}</p>
    <p v-else-if="message" class="message">{{ message }}</p>

    <div class="settings-grid">
      <section class="card">
        <h2>外观</h2>
        <p class="card-hint">深色/浅色主题，切换立即生效。</p>
        <div class="theme-switch">
          <button class="theme-option" :class="{ selected: theme === 'light' }" @click="applyTheme('light')">
            <span class="theme-swatch light"></span>
            浅色
          </button>
          <button class="theme-option" :class="{ selected: theme === 'dark' }" @click="applyTheme('dark')">
            <span class="theme-swatch dark"></span>
            深色
          </button>
        </div>

        <h2 class="section-title">模型参数</h2>
        <div class="field-row">
          <label class="field">
            <span>Temperature</span>
            <input v-model="temperature" class="text-input" inputmode="decimal" />
          </label>
          <label class="field">
            <span>Max Tokens</span>
            <input v-model="maxTokens" class="text-input" inputmode="numeric" />
          </label>
        </div>
        <p class="field-hint">留空则沿用全局 config。</p>
      </section>

      <section class="card">
        <h2>模型供应商</h2>
        <p class="card-hint">单个模型，演化与校验都用它；API Key 按供应商分别保存。</p>

        <div class="field-grid">
          <label class="field">
            <span>供应商</span>
            <a-select
              v-model:value="provider"
              class="select-input"
              :options="providerOptions"
              :dropdown-match-select-width="false"
              popup-class-name="oe-select-popup"
              @change="pickProvider"
            />
          </label>
          <label class="field">
            <span>模型名</span>
            <input v-model="modelName" class="text-input mono" placeholder="deepseek-chat" />
          </label>
        </div>

        <label class="field">
          <span>API Base</span>
          <input v-model="apiBase" class="text-input mono" placeholder="https://…" />
        </label>

        <label class="field">
          <span>API Key（{{ providerLabel }}）</span>
          <input v-model="apiKey" class="text-input mono" type="password" autocomplete="off" :placeholder="placeholder" />
        </label>
        <p class="field-hint">{{ keyHint }}</p>
        <p v-if="savedProviders" class="field-hint">已保存 Key 的供应商：{{ savedProviders }}（各自独立，切换不覆盖）。</p>
        <p v-if="envFile" class="field-hint">检测到 <code>.env.local</code>：其中的 <code>OPENEVOLVE_API_KEY</code> 优先级高于本页保存的 Key，且对所有供应商都生效。</p>

        <p class="field-hint">{{ keyNote }}</p>
      </section>
    </div>
  </section>
</template>

<style scoped>
.heading-actions { display:flex; gap:8px; }
.settings-grid { display:grid; grid-template-columns:minmax(250px, 1fr) minmax(0, 1.9fr); gap:16px; align-items:start; }
.settings-grid h2 { margin:0; font-size:14px; }
.section-title { margin-top:20px !important; }
.card-hint { margin:6px 0 14px; color:var(--muted); font-size:11px; }
.theme-switch { display:flex; gap:10px; }
.theme-option { display:flex; flex:1; align-items:center; justify-content:center; gap:8px; padding:12px; border:1px solid var(--line); border-radius:10px; background:var(--surface); color:inherit; font-size:12px; cursor:pointer; }
.theme-option.selected { border-color:var(--accent); background:#eef3ff; color:var(--accent); font-weight:700; }
.theme-swatch { width:16px; height:16px; border-radius:50%; border:1px solid var(--line); }
.theme-swatch.light { background:#fff; }
.theme-swatch.dark { background:#1b2438; }
.field-grid { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:10px 12px; }
.field { display:flex; flex-direction:column; gap:5px; margin-bottom:10px; }
.field > span { color:var(--muted); font-size:11px; }
.field-row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.field-hint { margin:6px 0 0; color:var(--muted); font-size:10px; line-height:1.6; }
.field-hint code { color:#3a4658; }
@media (max-width: 900px) {
  .settings-grid { grid-template-columns:1fr; }
  .field-grid { grid-template-columns:1fr; }
}
</style>
