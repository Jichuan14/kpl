import { createApp } from "vue";
import App from "./App.vue";
import "./style.css";
import { setupPageLocalization } from "./i18n";

createApp(App).mount("#app");
setupPageLocalization();
