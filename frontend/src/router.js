import { defineAsyncComponent } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import RouteLoadError from "./RouteLoadError.vue";
const lazy = (loader) => defineAsyncComponent({ loader, timeout: 15000, errorComponent: RouteLoadError });
export const routes = [
  { path: "/new/:section?/:view?", component: () => import("./NewAtlasPage.vue") },
  { path: "/", component: lazy(() => import("./HeroFeatureSpacePage.vue")) },
  { path: "/feature-space/:pathMatch(.*)*", component: lazy(() => import("./HeroFeatureSpacePage.vue")) },
  { path: "/bp-data/:pathMatch(.*)*", component: lazy(() => import("./VisualizationPage.vue")) },
  { path: "/simulator/:pathMatch(.*)*", component: lazy(() => import("./DraftSimulatorPage.vue")) },
  { path: "/teams/:pathMatch(.*)*", component: lazy(() => import("./TeamSynergyPage.vue")) },
  { path: "/rankings/:pathMatch(.*)*", component: lazy(() => import("./RankingsPage.vue")) },
  { path: "/methodology/:pathMatch(.*)*", component: lazy(() => import("./MethodologyPage.vue")) },
  { path: "/management/:pathMatch(.*)*", component: lazy(() => import("./ManagementPage.vue")) },
  { path: "/:pathMatch(.*)*", redirect: "/" },
];
export const router = createRouter({ history: createWebHistory(), routes, scrollBehavior: () => ({ top: 0, behavior: "smooth" }) });
