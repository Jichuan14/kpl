import { ref } from "vue";

// Page content owns its own loading state. A global veil delays navigation and
// hides useful layout while optional requests (such as history) are in flight.
export const startupLoading = ref(false);

export function finishStartupLoading() {
  startupLoading.value = false;
}
