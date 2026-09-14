#!/usr/bin/env node
"use strict";

// Load the same dependency-free state module QML executes, without a shell.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "..", "SpotifyState.js"), "utf8");
const context = {};
vm.createContext(context);
vm.runInContext(source, context, {filename: "SpotifyState.js"});

function test(name, body) {
  try {
    body();
    console.log(`ok - ${name}`);
  } catch (error) {
    console.error(`not ok - ${name}`);
    throw error;
  }
}

test("refresh work coalesces while mutations remain FIFO", () => {
  const current = {command: "playback", generation: 3};
  const coalesced = context.enqueue([], current, {command: "playback", generation: 3}, 8);
  assert.equal(coalesced.accepted, true);
  assert.equal(coalesced.queue.length, 0);

  const mutation = context.enqueue([{command: "next"}], null, {command: "pause"}, 8);
  assert.deepEqual(JSON.parse(JSON.stringify(mutation.queue)), [{command: "next"}, {command: "pause"}]);
});

test("queue cap rejects overflow without dropping earlier actions", () => {
  const original = [{command: "next"}, {command: "pause"}];
  const result = context.enqueue(original, null, {command: "play"}, 2);
  assert.equal(result.accepted, false);
  assert.deepEqual(JSON.parse(JSON.stringify(result.queue)), original);
});

test("artwork permits only Spotify HTTPS hosts", () => {
  assert.equal(context.artUrl("https://i.scdn.co/image/abc"), "https://i.scdn.co/image/abc");
  assert.equal(context.artUrl("https://image-cdn-fa.spotifycdn.com/image/abc"), "https://image-cdn-fa.spotifycdn.com/image/abc");
  assert.equal(context.artUrl("file:///home/user/private.png"), "");
  assert.equal(context.artUrl("https://spotify.example/image.png"), "");
});

test("local MPRIS is authoritative only for the confirmed local device", () => {
  const local = {trackTitle: "Local", trackArtist: "Artist", length: 120, position: 10, isPlaying: true};
  const api = {device: {id: "1", name: "Oma Spotify", volume_percent: 50}, item: {name: "Remote"}};
  const localView = context.playbackView(api, local, true, "Oma Spotify", null);
  assert.equal(localView.source, "local");
  assert.equal(localView.title, "Local");

  api.device.name = "Phone";
  const remoteView = context.playbackView(api, local, true, "Oma Spotify", null);
  assert.equal(remoteView.source, "api");
  assert.equal(remoteView.title, "Remote");
});

test("auth and rate-limit failures preserve stale playback safely", () => {
  const auth = context.failureState({kind: "auth", message: "Login again"}, true, 1000);
  assert.equal(auth.stale, true);
  assert.equal(auth.authenticated, false);
  const limited = context.failureState({kind: "rate_limit", retry_after: 2, message: "Wait"}, true, 1000);
  assert.equal(limited.authenticated, true);
  assert.equal(limited.retryAt, 3000);
});
