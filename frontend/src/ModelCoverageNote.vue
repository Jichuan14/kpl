<script setup>
import { computed } from "vue";
import { t } from "./i18n";
const props = defineProps({ metadata: { type: Object, default: null } });
const coverage = computed(() => {
  const seasons = props.metadata?.source_seasons || [];
  return seasons.length ? `${seasons[0]} – ${seasons.at(-1)}` : "";
});
</script>
<template>
  <small v-if="metadata?.model_version" :title="metadata.model_version">
    {{ t("Active model coverage") }}: {{ coverage }} ·
    {{ t("Historical reference through") }} {{ metadata.context_reference_cutoff?.slice(0, 10) }}
    <template v-if="metadata.calibration_status === 'uncalibrated'"> · {{ t("Uncalibrated probabilities") }}</template>
  </small>
</template>
