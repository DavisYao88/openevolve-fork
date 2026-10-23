import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import EvolutionView from "./views/EvolutionView.vue";
import ManualView from "./views/ManualView.vue";
import RunsView from "./views/RunsView.vue";
import RunDetailView from "./views/RunDetailView.vue";
import ProgramView from "./views/ProgramView.vue";
import "./style.css";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: EvolutionView },
    { path: "/manual", component: ManualView },
    { path: "/launch", redirect: "/runs" },
    { path: "/runs", component: RunsView },
    { path: "/runs/:id", component: RunDetailView },
    { path: "/program/:id", component: ProgramView },
  ],
});

// 可视化页只能通过任务列表的「查看」进入；直接访问 / 时回到任务列表，
// 否则后端会回落到 examples/ 下的旧示例数据。
router.beforeEach((to) => {
  if (to.path === "/" && !to.query.run_id) return { path: "/runs" };
  return true;
});

createApp(App).use(router).mount("#app");
