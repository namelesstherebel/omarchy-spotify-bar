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
process.exitCode = failures ? 1 : 0;
