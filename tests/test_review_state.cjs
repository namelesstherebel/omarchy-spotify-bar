#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '..');
const state = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(root, 'SpotifyState.js'), 'utf8'), state);
let failures = 0;
function test(name, body) {
  try { body(); console.log('ok - ' + name); }
  catch (e) { failures++; console.error('not ok - ' + name + ': ' + e.message); }
}
const device = {id: 'remote', name: 'Oma Spotify', is_restricted: false};
const playback = {device, item: null, is_playing: false, progress_ms: 0};
test('strict command-specific envelopes reject malformed helper output', () => {
  for (const raw of ['null', '[]', 'true', '{}', '{"ok":1,"data":null}',
      '{"ok":true}', '{"ok":false}', '{"ok":false,"error":{"kind":"auth","message":42}}',
      '{"ok":true,"data":null,"error":{}}']) {
    assert.equal(state.result(raw, 'playback', {}).ok, false, raw);
  }
  assert.equal(state.result(JSON.stringify({ok: true, data: playback}), 'playback', {}).ok, true);
  assert.equal(state.result('{"ok":true,"data":null}', 'playback', {}).ok, true);
  for (const [command, data, args] of [
    ['status', {}, {}], ['login', {authenticated: false}, {}],
    ['devices', {devices: {}}, {}], ['devices', {devices: [null]}, {}],
    ['devices', {devices: [{id:'x', name:'Missing restrictions'}]}, {}],
    ['playback', {...playback, item: {uri:'spotify:track:abc', name:'x', artists:{}}}, {}],
    ['playback', {...playback, device: {...device, is_restricted: 'false'}}, {}],
    ['library', {items: 'bad'}, {}], ['queue', {queue: {}}, {}],
    ['search', {tracks: {items: [{uri:'spotify:track:abc',name:'x',images:[null]}]}}, {kind:'track'}],
    ['next', {}, {}]
  ]) assert.equal(state.result(JSON.stringify({ok:true,data}), command, args).ok, false, command);
});
test('state transformations reject non-array collections without throwing', () => {
  assert.equal(state.page({items:{}}).items.length, 0);
  assert.equal(state.itemView({uri:'spotify:track:abc',name:'x',artists:{}}), null);
  assert.equal(state.playbackView({...playback,item:{artists:{},images:[null]}}, null).artist, '');
});
test('same-name remote device stays API-controllable when spotifyd is down', () => {
  const view = state.playbackView(playback, null);
  assert.equal(view.canControl, true);
  assert.equal(view.canVolume, true);
});
test('a device name is not proof of local MPRIS association', () => {
  const view = state.playbackView(playback, {id:'other', name:'Oma Spotify', is_restricted:false});
  assert.equal(view.deviceId, 'remote');
  assert.equal(view.source, 'api');
});
test('valid list wrappers, unavailable items and selected-device controls survive validation', () => {
  const item = {uri:'spotify:track:abc',name:'<b>Title</b>',artists:[{name:'<i>Artist</i>'}],album:{images:[]}};
  for (const [command, data, args] of [
    ['library',{items:[{track:item}, {track:null}, null],next:null},{kind:'tracks'}],
    ['browse',{items:[item],next:null},{}],
    ['queue',{queue:[item],currently_playing:null},{}],
    ['search',{tracks:{items:[item],offset:0,limit:10,next:'https://api.spotify.com/v1/search?offset=10'}},{kind:'track'}],
    ['devices',{devices:[device]},{}], ['next',null,{}]
  ]) assert.equal(state.result(JSON.stringify({ok:true,data}),command,args).ok,true,command);
  assert.equal(state.playbackView(null, device).canControl, true);
  assert.equal(state.playbackView(null, {...device,is_restricted:true}).canControl, false);
  assert.equal(state.page({items:[{track:item},null]}).items[0].title,'<b>Title</b>');
});
test('popup owners are idempotent and closing one does not close another', () => {
  const a = {}, b = {};
  let owners = state.setOwner([], a, true);
  owners = state.setOwner(owners, a, true);
  owners = state.setOwner(owners, b, true);
  owners = state.setOwner(owners, a, false);
  assert.equal(owners.length, 1);
  assert.equal(owners[0], b);
  assert.equal(state.setOwner(owners, b, false).length, 0);
});
function bridge() {
  const source = fs.readFileSync(path.join(root, 'Service.qml'), 'utf8');
  const ctx = vm.createContext({State:state, Date, JSON,
    current:{command:'next',args:{}}, pending:[{command:'next',args:{}}],
    authenticated:true, configured:true, panelOpen:false, retryAt:0,
    apiPlayback:playback, selectedDevice:device, devices:[device], items:[{title:'Preserved'}],
    playback:state.playbackView(playback, null), stale:false, busy:false,
    helperTimedOut:false, helper:{running:true, signals:[], signal(n) {this.killed=n; this.signals.push(n);}},
    watchdog:{stop(){},restart(){}}, pump(){}, request(){throw Error('mutation replayed');}});
  for (const name of ['complete','expire','transport']) {
    const match = source.match(new RegExp('^    function '+name+'\\([^]*?^    }', 'm'));
    if (match) vm.runInContext(match[0], ctx);
  }
  return ctx;
}
test('watchdog kills once and ignores successful late mutation output', () => {
  const ctx = bridge();
  ctx.expire();
  assert.equal(ctx.helper.killed, 9);
  ctx.expire();
  assert.equal(ctx.helper.signals.length, 1);
  assert.equal(ctx.pending.length, 0);
  assert.notEqual(ctx.current, null); // stay serialized until reaped
  ctx.complete('{"ok":true,"data":null}', 0);
  assert.equal(ctx.current, null);
  assert.equal(ctx.stale, true);
  assert.equal(ctx.suspended, true);
  assert.equal(ctx.authenticated, true);
  ctx.complete('{"ok":true,"data":null}', 0); // duplicate exit is harmless
});
test('watchdog clears a helper that failed to start without waiting for an exit', () => {
  const ctx = bridge();
  ctx.helper.running = false;
  ctx.expire();
  assert.equal(ctx.current, null);
  assert.equal(ctx.pending.length, 0);
  assert.equal(ctx.suspended, true);
});
test('invalid envelopes clear work without throwing or losing authentication', () => {
  for (const raw of ['null','[]','{"ok":true}','{"ok":true,"data":{}}']) {
    const ctx = bridge();
    ctx.complete(raw, 0);
    assert.equal(ctx.current, null);
    assert.equal(ctx.pending.length, 0);
    assert.equal(ctx.authenticated, true);
    assert.equal(ctx.stale, true);
    assert.equal(ctx.suspended, true);
    assert.equal(ctx.apiPlayback, playback);
    assert.equal(ctx.items[0].title, 'Preserved');
  }
});
test('nonzero exit cannot acknowledge a mutation', () => {
  const ctx = bridge();
  ctx.complete('{"ok":true,"data":null}', 1);
  assert.equal(ctx.stale, true);
  assert.equal(ctx.suspended, true);
  assert.equal(ctx.pending.length, 0);
});
test('late malformed state never partially overwrites playback or devices', () => {
  for (const [command, data] of [['playback', {...playback,is_playing:'false'}],
      ['devices', {devices:[device,null]}], ['library', {items:[null,42]}]]) {
    const ctx = bridge();
    ctx.current = {command,args:{}};
    ctx.complete(JSON.stringify({ok:true,data}), 0);
    assert.equal(ctx.apiPlayback, playback);
    assert.equal(ctx.devices[0], device);
    assert.equal(ctx.items[0].title, 'Preserved');
    assert.equal(ctx.suspended, true);
    assert.equal(ctx.pending.length, 0);
  }
});
test('transport always targets the API ID and respects safety gates', () => {
  const ctx = bridge();
  const calls = [];
  ctx.request = (command,args) => calls.push({command,args});
  ctx.localPlayer = {next() {throw Error('unsafe MPRIS route');}};
  ctx.localAvailable = false;
  ctx.transport('next', {});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].args.device_id, 'remote');
  for (const gate of ['busy','stale']) {
    ctx[gate] = true;
    ctx.transport('next', {});
    ctx[gate] = false;
  }
  ctx.retryAt = Date.now() + 60000;
  ctx.transport('next', {});
  ctx.retryAt = 0;
  ctx.playback = state.playbackView({...playback,device:{...device,is_restricted:true}}, null);
  ctx.transport('next', {});
  assert.equal(calls.length, 1);
});
// Execute the real service methods with a fake serialized process. No helper is
// launched; completions below are synthetic Spotify-shaped pages.
function listBridge() {
  const source = fs.readFileSync(path.join(root, 'Service.qml'), 'utf8');
  const ctx = vm.createContext({State:state, Date, JSON,
    panelOwners:[], authenticated:true, retryAt:0, pending:[], current:null,
    deviceGeneration:0,
    helperTimedOut:false, watchdog:{stop(){}, restart(){}},
    helper:{running:true, signals:[], signal(n){this.signals.push(n);}}, errorMessage:'', stale:false,
    playback:state.playbackView(playback, null)});
  Object.defineProperty(ctx, 'panelOpen', {get:() => ctx.panelOwners.length > 0});
  Object.defineProperty(ctx, 'busy', {get:() => ctx.current !== null});
  for (const match of source.matchAll(/^    function \w+\([^]*?^    }/gm))
    vm.runInContext(match[0], ctx);
  ctx.pump = () => {
    if (!ctx.current && ctx.pending.length) {
      ctx.current = ctx.pending[0]; ctx.pending = ctx.pending.slice(1);
      ctx.helperTimedOut = false;
    }
  };
  return ctx;
}
function listOwner() {
  return {items:[], view:'tracks', query:'', searchKind:'track', browseUri:'',
    nextOffset:-1, before:'', listGeneration:0};
}
function finishList(ctx, title, next = false) {
  const job = ctx.current;
  const page = {items:[{uri:'spotify:track:' + title, name:title}], offset:job.args.offset || 0,
    limit:2, next:next ? 'https://api.spotify.com/v1/me/tracks?offset=2' : null};
  const data = job.command === 'search' ? {[job.args.kind + 's']:page} : page;
  ctx.complete(JSON.stringify({ok:true, data}), 0);
}
for (const closing of ['library', 'search']) {
  test('two popups keep Library/Search results, actions and pages isolated; close ' + closing, () => {
    const ctx = listBridge(), library = listOwner(), search = listOwner();
    ctx.setPanelOpen(library, true); ctx.setPanelOpen(search, true);
    ctx.load(library, 'tracks', '', false);
    ctx.load(search, 'search', 'needle', false);
    assert.equal(ctx.current.owner, library, 'active Library request retains its owner');
    assert.equal(ctx.pending[0].owner, search, 'concurrent Search remains queued for its owner');
    assert.equal(ctx.listBusy(library), true);
    assert.equal(ctx.listBusy(search), true);
    finishList(ctx, 'Library', true);
    assert.equal(ctx.listBusy(library), false);
    assert.equal(ctx.listBusy(search), true);
    assert.equal(library.items[0].title, 'Library');
    assert.equal(search.items.length, 0);
    finishList(ctx, 'Search', true);
    assert.equal(library.view, 'tracks');
    assert.equal(search.view, 'search');
    assert.equal(search.query, 'needle');
    assert.equal(library.items[0].title, 'Library');
    assert.equal(search.items[0].title, 'Search');
    const actions = [], request = ctx.request;
    ctx.request = (command, args) => actions.push({command, args});
    ctx.playItem(library.items[0]); ctx.playItem(search.items[0]);
    assert.equal(actions[0].args.uri, 'spotify:track:Library');
    assert.equal(actions[1].args.uri, 'spotify:track:Search');
    // Restore real requests, then close either owner while its page is active.
    ctx.request = request;
    const closed = closing === 'library' ? library : search;
    const survivor = closing === 'library' ? search : library;
    ctx.load(closed, closed.view, closed.query, true);
    assert.equal(ctx.current.args.offset, 2);
    ctx.load(survivor, survivor.view, survivor.query, true);
    ctx.setPanelOpen(closed, false);
    assert.equal(ctx.panelOpen, true);
    assert.deepEqual(ctx.helper.signals, [9], 'only the closed owner is killed');
    assert.equal(ctx.listBusy(closed), false);
    assert.equal(ctx.listBusy(survivor), true);
    assert.equal(ctx.pending[0].owner, survivor, 'closing one preserves the other page');
    const snapshot = JSON.stringify(closed.items);
    finishList(ctx, 'ClosedLate');
    assert.equal(JSON.stringify(closed.items), snapshot, 'closed owner ignores late completion');
    assert.equal(ctx.current.owner, survivor);
    finishList(ctx, 'More');
    assert.equal(survivor.items.length, 2);
    assert.equal(survivor.items[1].title, 'More');
    assert.equal(survivor.nextOffset, -1);
    assert.equal(ctx.stale, false);
    ctx.setPanelOpen(survivor, false);
    assert.equal(ctx.panelOpen, false);
  });
}
test('same-command jobs never coalesce across popup owners', () => {
  const a = listOwner(), b = listOwner();
  for (const command of ['search', 'queue']) {
    const first = {command, owner:a, generation:1};
    const second = {command, owner:b, generation:1};
    assert.equal(state.enqueue([], first, second, 8).queue[0], second);
    assert.equal(state.enqueue([first], null, second, 8).queue.length, 2);
    assert.equal(state.enqueue([first], null, {...first, generation:2}, 8).queue.length, 1);
  }
});
test('closing a queued owner preserves active list, devices and transport work', () => {
  const ctx = listBridge(), a = listOwner(), b = listOwner();
  ctx.setPanelOpen(a, true); ctx.setPanelOpen(b, true);
  ctx.load(a, 'tracks', '', false); ctx.load(b, 'search', 'needle', false);
  ctx.request('devices', {}); ctx.request('next', {});
  ctx.setPanelOpen(b, false);
  assert.equal(ctx.current.owner, a);
  assert.equal(ctx.current.cancelled, undefined);
  assert.equal(ctx.helper.signals.length, 0);
  assert.deepEqual(Array.from(ctx.pending, job => job.command), ['devices', 'next']);
  assert.equal(ctx.load(b, 'tracks', '', false), undefined);
  assert.equal(ctx.request('library', {kind:'tracks'}, b.listGeneration, false, b), false);
  assert.equal(ctx.request('library', {kind:'tracks'}), false);
  finishList(ctx, 'Library');
  assert.equal(a.items[0].title, 'Library');
});
test('reopen and supersede ignore late output including errors, without stopping another owner', () => {
  for (const raw of ['not JSON', '{"ok":false,"error":{"kind":"auth","message":"Login","retry_after":0}}']) {
    const ctx = listBridge(), a = listOwner(), b = listOwner();
    ctx.setPanelOpen(a, true); ctx.setPanelOpen(b, true);
    ctx.load(a, 'tracks', '', false); ctx.load(b, 'search', 'other', false);
    ctx.setPanelOpen(a, false); ctx.setPanelOpen(a, true);
    ctx.load(a, 'search', 'new', false);
    assert.equal(ctx.current.cancelled, true);
    assert.equal(ctx.current.owner, null);
    ctx.complete(raw, 1);
    assert.equal(ctx.authenticated, true);
    assert.equal(ctx.stale, false);
    assert.equal(ctx.current.owner, b);
    finishList(ctx, 'Other'); finishList(ctx, 'New');
    assert.equal(a.items[0].title, 'New');
    assert.equal(b.items[0].title, 'Other');
    ctx.load(a, 'search', 'obsolete', false);
    ctx.load(a, 'search', 'latest', false);
    ctx.complete(raw, 1);
    finishList(ctx, 'Latest');
    assert.equal(a.items[0].title, 'Latest');
    assert.equal(b.items[0].title, 'Other');
  }
});
test('completion generation guard rejects uncancelled stale pages', () => {
  const ctx = listBridge(), a = listOwner();
  ctx.setPanelOpen(a, true); ctx.load(a, 'tracks', '', false);
  a.listGeneration++;
  finishList(ctx, 'Stale');
  assert.equal(a.items.length, 0);
});
test('browse URI, search kind and recent cursor belong to their own popup', () => {
  const ctx = listBridge(), a = listOwner(), b = listOwner();
  ctx.setPanelOpen(a, true); ctx.setPanelOpen(b, true);
  ctx.browse(a, {uri:'spotify:album:Example'});
  b.searchKind = 'artist'; ctx.load(b, 'search', 'artist', false);
  assert.equal(ctx.current.args.uri, 'spotify:album:Example');
  finishList(ctx, 'AlbumTrack', true);
  assert.equal(ctx.current.args.kind, 'artist');
  finishList(ctx, 'Artist');
  ctx.load(a, a.view, a.query, true);
  assert.equal(ctx.current.args.uri, 'spotify:album:Example');
  assert.equal(ctx.current.args.offset, 2);
  finishList(ctx, 'More');
  ctx.load(a, 'recent', '', false);
  ctx.complete(JSON.stringify({ok:true,data:{items:[],cursors:{before:'123'},next:'https://api.spotify.com/v1/me/player/recently-played?before=123'}}), 0);
  ctx.load(a, a.view, a.query, true);
  assert.equal(ctx.current.args.before, '123');
  assert.equal(ctx.current.args.offset, undefined);
  assert.equal(b.before, '');
  assert.equal(b.query, 'artist');
});
test('queue additions refresh open queue owners without replacing a Search view', () => {
  const ctx = listBridge(), queue = listOwner(), search = listOwner();
  ctx.setPanelOpen(queue, true); ctx.setPanelOpen(search, true);
  queue.view = 'queue';
  ctx.load(search, 'search', 'needle', false); finishList(ctx, 'Search');
  ctx.request('enqueue', {uri:'spotify:track:Example'});
  ctx.complete('{"ok":true,"data":null}', 0);
  assert.equal(ctx.current.command, 'playback');
  assert.equal(ctx.pending.length, 1);
  assert.equal(ctx.pending[0].command, 'queue');
  assert.equal(ctx.pending[0].owner, queue);
  assert.equal(search.view, 'search');
  assert.equal(search.items[0].title, 'Search');
});
process.exitCode = failures ? 1 : 0;
