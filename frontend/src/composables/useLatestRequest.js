import { onBeforeUnmount } from "vue";
export function useLatestRequest() {
  let controller = null;
  let version = 0;
  onBeforeUnmount(() => controller?.abort());
  return async (load) => {
    controller?.abort(); controller = new AbortController();
    const current = ++version;
    try { return await load(controller.signal, () => current === version); }
    finally { if (current === version) controller = null; }
  };
}
