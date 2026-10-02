import { computed, ref } from "vue";
import * as defaultApi from "../api.js";
import { publicSeasonState } from "../selectedLeague.js";
import { createManagementSeasonState } from "../managementSeasonState.js";
import { language, t } from "../i18n.js";
import { usePolling } from "./usePolling.js";
import { loadSelectedStatus } from "../managementStatus.js";

// Keep job state while the management route is unmounted. The server ledger is
// authoritative and restores an in-progress job after a browser reload.
let singleton;
export function createManagement(overrides = {}) {
  const { fetchDataStatus, fetchLeagues, fetchSiteDefault, saveSiteDefault, fetchCoachUsage, fetchVisitorStats, updateCoachLimits, syncLeagues, syncLeagueBp, runAnalysisStep, publishFrontendAssets, fetchPipelineJobs, queueFullUpdate, invalidatePublishedData } = { ...defaultApi, ...overrides };
  const polling = overrides.polling || usePolling;
  const seasonState = createManagementSeasonState(fetchLeagues, fetchSiteDefault, saveSiteDefault, overrides.onSavedDefault);
  const { leagueId, leagues, selectedYear, savingSiteDefault } = seasonState;
  const dataStatus = ref(null);
  let statusVersion = 0;
  const coachUsage = ref(null), visitorAnalytics = ref(null), coachLimits = ref(null);
  const loading = ref(false), syncing = ref(false), syncingCatalog = ref(false), savingCoachLimits = ref(false);
  const processingStep = ref(""), processingElapsed = ref(0), syncMode = ref(""), syncElapsed = ref(0);
  const error = ref(""), notice = ref(""), apiConnected = ref(false), recentJobs = ref([]), activeJob = ref(null);
  const years = computed(() => [...new Set(leagues.value.map((l) => l.year).filter(Boolean))].sort((a,b) => b-a));
  const seasonLeagues = computed(() => leagues.value.filter((l) => !selectedYear.value || String(l.year) === selectedYear.value));
  const selectedLeague = computed(() => leagues.value.find((l) => l.league_id === leagueId.value));
  const analysisPipeline = computed(() => (dataStatus.value?.pipeline || []).filter((s) => !["download", "players"].includes(s.key)));
  const readyStages = computed(() => analysisPipeline.value.filter((s) => s.ready).length), totalStages = computed(() => analysisPipeline.value.length);
  const frontendAssets = computed(() => dataStatus.value?.frontend_assets || []), frontendAssetsReady = computed(() => frontendAssets.value.filter((s) => s.ready).length);
  const artifacts = computed(() => Object.values(dataStatus.value?.artifacts || {}).flat().filter((x) => x && typeof x === "object" && (x.path || x.key)));
  async function loadLeagues() { await seasonState.loadLeagues(); apiConnected.value = true; }
  async function loadStatus(operationLeagueId = leagueId.value) {
    if (typeof operationLeagueId !== "string") operationLeagueId = leagueId.value;
    const version = ++statusVersion;
    if (!operationLeagueId) return;
    loading.value = true;
    try {
      await loadSelectedStatus(fetchDataStatus, operationLeagueId, () => version === statusVersion ? leagueId.value : "", (status) => { dataStatus.value = status; });
    } catch (e) {
      if (version === statusVersion && operationLeagueId === leagueId.value) error.value = e.message || "Could not load local data status.";
    } finally { if (version === statusVersion) loading.value = false; }
  }
  async function chooseManagementLeague(id) {
    dataStatus.value = null;
    const saved = await seasonState.chooseLeague(id);
    if (seasonState.defaultError.value) error.value = seasonState.defaultError.value;
    else if (saved) { error.value = ""; notice.value = t("Site default season saved."); }
    dataStatus.value = null;
    await loadStatus();
  }
  async function chooseManagementYear(year) {
    dataStatus.value = null;
    const saved = await seasonState.chooseYear(year);
    if (seasonState.defaultError.value) error.value = seasonState.defaultError.value;
    else if (saved) { error.value = ""; notice.value = t("Site default season saved."); }
    dataStatus.value = null;
    await loadStatus();
  }
  async function loadCoachUsage() { try { coachUsage.value=await fetchCoachUsage(); coachLimits.value ||= { ip_requests_per_minute: coachUsage.value.per_ip.per_minute_limit, ip_requests_per_day: coachUsage.value.per_ip.per_24_hours_limit, server_requests_per_minute: coachUsage.value.server.per_minute_limit, server_requests_per_day: coachUsage.value.server.per_24_hours_limit, ip_max_active_requests: coachUsage.value.per_ip.max_active_requests, server_max_active_requests: coachUsage.value.server.max_active_requests }; } catch {} }
  async function loadVisitorAnalytics() { try { visitorAnalytics.value=await fetchVisitorStats(); } catch {} }
  const coachPolling=polling(loadCoachUsage,15000), visitorPolling=polling(loadVisitorAnalytics,30000);
  const jobsPolling=polling(loadJobs,3000);
  function applyBusy(job) {
    const busy = job && ["pending", "running"].includes(job.status);
    syncing.value = Boolean(busy && ["sync_bp", "scheduled", "full_update", "sync_leagues"].includes(job.kind));
    syncingCatalog.value = Boolean(busy && job.kind === "sync_leagues");
    syncMode.value = busy && job.kind === "sync_bp" ? (job.payload?.match_limit === 5 ? "sample" : "all") : "";
    processingStep.value = !busy ? "" : job.kind === "full_update" || job.kind === "scheduled" ? "full_update" : job.kind === "analysis" ? job.payload?.step || "all" : job.kind === "publish" ? "publish" : "";
    const elapsed = busy && job.created_at ? Math.max(0, Math.floor((Date.now() - Date.parse(job.created_at)) / 1000)) : 0;
    processingElapsed.value = elapsed;
    syncElapsed.value = elapsed;
  }
  async function loadJobs() {
    try {
      const jobs = await fetchPipelineJobs() || [];
      recentJobs.value = jobs;
      const previous = activeJob.value;
      const chosen = jobs.find((job) => ["pending", "running"].includes(job.status) && (job.league_id === leagueId.value || job.kind === "sync_leagues" || (job.kind === "scheduled" && !job.league_id)));
      const updated = previous && jobs.find((job) => job.id === previous.id);
      activeJob.value = chosen || updated || null;
      applyBusy(chosen);
      if (updated && previous.status !== updated.status && ["completed", "failed"].includes(updated.status)) {
        if (updated.status === "failed") { error.value = updated.error || "Pipeline job failed."; notice.value = ""; }
        else {
          notice.value = `${updated.kind} completed`; error.value = ""; invalidatePublishedData();
          await loadLeagues(); await loadStatus(leagueId.value);
        }
      }
    } catch (e) { if (activeJob.value) error.value = e.message || "Could not load job status."; }
  }
  async function initialize() { await loadLeagues(); await Promise.all([loadStatus(),loadCoachUsage(),loadVisitorAnalytics(),loadJobs()]); }
  function startMonitoring() { coachPolling.start(); visitorPolling.start(); jobsPolling.start(); }
  function stopMonitoring() { coachPolling.stop(); visitorPolling.stop(); jobsPolling.stop(); }
  async function submit(action, label) {
    if (savingSiteDefault.value) return null;
    error.value = "";
    try {
      const job = await action();
      activeJob.value = job;
      applyBusy(job);
      notice.value = `${label} · ${job.status}`;
      await loadJobs();
      return job;
    } catch (e) { notice.value = ""; error.value = e.message || `${label} failed`; return null; }
  }
  async function refreshLeagueCatalog() { await submit(() => syncLeagues(), "League catalog refresh"); }
  async function runDownload({matchLimit=null,mode="all"}={}) { if(!leagueId.value || syncing.value || processingStep.value) return; syncMode.value=mode; const operationLeagueId = leagueId.value; await submit(() => syncLeagueBp({leagueId:operationLeagueId,matchLimit}), mode === "all" ? "League download" : "Sample download"); }
  async function runPipeline(step) { if(!leagueId.value || syncing.value || processingStep.value) return; const operationLeagueId = leagueId.value; await submit(() => runAnalysisStep({leagueId:operationLeagueId,step}), `${step} analysis`); }
  async function publishAssets() { if(!leagueId.value || syncing.value || processingStep.value) return; const operationLeagueId = leagueId.value; await submit(() => publishFrontendAssets(operationLeagueId), "Asset publication"); }
  async function runFullUpdate() { if(!leagueId.value || syncing.value || processingStep.value) return; const operationLeagueId = leagueId.value; await submit(() => queueFullUpdate(operationLeagueId), "Full update"); }
  async function saveCoachLimits(){ if(!coachLimits.value)return; savingCoachLimits.value=true; try{coachUsage.value=await updateCoachLimits(coachLimits.value);}finally{savingCoachLimits.value=false;} }
  const pipelineReady=(key)=>Boolean(dataStatus.value?.pipeline?.find((s)=>s.key===key)?.ready);
  const number=(v)=>Number(v||0).toLocaleString(language.value), bytes=(v)=>!v?"—":`${(Number(v)/1024).toFixed(1)} KB`, dateTime=(v)=>v?new Date(v).toLocaleString(language.value):"Never";
  const state={savingSiteDefault,chooseManagementLeague,chooseManagementYear,leagueId,leagues,selectedYear,dataStatus,coachUsage,visitorAnalytics,coachLimits,loading,syncing,syncingCatalog,savingCoachLimits,processingStep,processingElapsed,syncMode,syncElapsed,error,notice,apiConnected,recentJobs,activeJob,years,seasonLeagues,selectedLeague,analysisPipeline,readyStages,totalStages,frontendAssets,frontendAssetsReady,artifacts,loadLeagues,loadStatus,loadJobs,loadCoachUsage,loadVisitorAnalytics,refreshLeagueCatalog,runDownload,runPipeline,runFullUpdate,publishAssets,saveCoachLimits,pipelineReady,number,bytes,dateTime,initialize,startMonitoring,stopMonitoring}; return state;
}

export function useManagement() { singleton ||= createManagement({ onSavedDefault: publicSeasonState.applySavedDefault }); return singleton; }
