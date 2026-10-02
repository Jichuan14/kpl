// A disposed view or changed input can never accept an older async result.
export function createRequestScope() {
  let version = 0;
  let controller = null;
  let disposed = false;
  function invalidate() {
    version += 1;
    controller?.abort();
    controller = null;
  }
  return {
    get disposed() { return disposed; },
    invalidate,
    begin() {
      if (disposed) return null;
      invalidate();
      controller = new AbortController();
      const current = version;
      return { signal: controller.signal, isCurrent: () => !disposed && current === version };
    },
    dispose() { disposed = true; invalidate(); },
  };
}
