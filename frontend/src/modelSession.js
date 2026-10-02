// A mounted tool/draft owns this pin. Failed or retired versions never silently
// switch to a newer active model midway through a recommendation/scenario.
export function createModelSession(loadActive) {
  let pending;
  return {
    version() {
      pending ||= Promise.resolve().then(loadActive).then((metadata) => {
        if (!metadata?.model_version) throw new Error("No active production model is available.");
        return metadata.model_version;
      });
      return pending;
    },
  };
}
