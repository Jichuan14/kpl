import test from 'node:test';
import assert from 'node:assert/strict';
import { ref, watch } from 'vue';
import { createPublicSeasonState } from './publicSeasonState.js';
import { createSeasonStartup } from './seasonStartup.js';

test('initial catalog selection loads each page once; later selections load normally', async () => {
  const state = createPublicSeasonState(async () => [{league_id: 's4'}], async () => ({default_league_id:'s4'}));
  const calls = [];
  const startup = createSeasonStartup(state.loadSeasons, () => calls.push(state.selectedLeagueId.value));
  const stop = watch(state.selectedLeagueId, startup.changed, {flush:'sync'});
  await Promise.all([state.loadSeasons(), startup.initialize()]);
  assert.deepEqual(calls, ['s4']);
  state.selectLeague('s3'); assert.deepEqual(calls, ['s4','s3']);
  stop();
});

test('mounted page recovers when catalog fails then another caller retries or saves', async () => {
  let attempts = 0;
  const state = createPublicSeasonState(async () => { if (++attempts === 1) throw Error('offline'); return []; }, async () => ({default_league_id:'s4'}));
  const calls = [];
  const startup = createSeasonStartup(state.loadSeasons, () => calls.push(state.selectedLeagueId.value));
  const stop = watch(state.selectedLeagueId, startup.changed, {flush:'sync'});
  await assert.rejects(startup.initialize(), /offline/);
  assert.deepEqual(calls, []);
  await state.loadSeasons(); assert.deepEqual(calls, ['s4']);
  state.applySavedDefault('s3'); assert.deepEqual(calls, ['s4','s3']);
  stop();
});
