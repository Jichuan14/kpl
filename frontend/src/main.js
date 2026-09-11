import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
import "./style.css";
import { setupPageLocalization } from "./i18n";

createApp(App).use(router).mount("#app");
setupPageLocalization();
