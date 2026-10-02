// Initialization owns the first page load. Watchers resume even if the catalog
// fails, so a later public selection or shared retry can recover the page.
export function createSeasonStartup(loadCatalog, loadPage) {
  let ready = false;
  return {
    async initialize() {
      try { await loadCatalog(); }
      finally { ready = true; }
      return loadPage();
    },
    changed() { if (ready) return loadPage(); },
  };
}
