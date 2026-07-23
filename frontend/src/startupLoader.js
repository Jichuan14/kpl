import { ref } from "vue";

const minimumDisplayMs = 700;
const startedAt = performance.now();
let resolved = false;

export const startupLoading = ref(true);

export function finishStartupLoading() {
  if (resolved) return;
  resolved = true;
  const remaining = Math.max(0, minimumDisplayMs - (performance.now() - startedAt));
  window.setTimeout(() => {
    startupLoading.value = false;
  }, remaining);
}
