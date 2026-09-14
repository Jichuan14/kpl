import { onBeforeUnmount } from "vue";
export function usePolling(load, interval) {
  let timer = null; let pending = false;
  async function tick() { if (pending) return; pending = true; try { await load(); } finally { pending = false; } }
  function start() { if (!timer) { tick(); timer = window.setInterval(tick, interval); } }
  function stop() { if (timer) window.clearInterval(timer); timer = null; }
  onBeforeUnmount(stop); return { start, stop, tick };
}
