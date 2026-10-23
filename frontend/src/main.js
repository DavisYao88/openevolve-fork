import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import EvolutionView from "./views/EvolutionView.vue";
import ManualView from "./views/ManualView.vue";
import LaunchView from "./views/LaunchView.vue";
import RunsView from "./views/RunsView.vue";
import RunDetailView from "./views/RunDetailView.vue";
import ProgramView from "./views/ProgramView.vue";
import "./style.css";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: EvolutionView },
    { path: "/manual", component: ManualView },
    { path: "/launch", component: LaunchView },
    { path: "/runs", component: RunsView },
    { path: "/runs/:id", component: RunDetailView },
    { path: "/program/:id", component: ProgramView },
  ],
});

createApp(App).use(router).mount("#app");
