<script setup>
defineProps({ error: { type: Error, default: null } });
function retry() { window.location.reload(); }
</script>
<template>
  <main class="route-load-error" role="alert">
    <h1>{{ $t("Unable to load this page / 页面暂时无法加载") }}</h1>
    <p>{{ $t("Please retry after checking your connection. 请检查网络后重试。") }}</p>
    <button type="button" @click="retry">{{ $t("Retry / 重试") }}</button>
  </main>
</template>
