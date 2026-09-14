// Pure state transformations shared by the QML bridge and headless tests.

// Bound pending work. Repeated refreshes coalesce; a newer pending search wins.
// Mutations remain FIFO and are never silently replaced or replayed.
function enqueue(queue, current, request, cap) {
    var result = queue.slice()
    var refresh = ["status", "playback", "devices", "queue"].indexOf(request.command) >= 0
    if (refresh && current && current.command === request.command && current.generation === request.generation)
        return {queue: result, accepted: true}
    for (var i = 0; i < result.length; i++) {
        if (result[i].command !== request.command) continue
        // A reopened queue/device view needs its own response. Replace obsolete
        // pending work, but do not coalesce it with an older active generation.
        if (refresh || request.command === "search") {
            result[i] = request
            return {queue: result, accepted: true}
        }
    }
    if (result.length >= cap) return {queue: result, accepted: false}
    result.push(request)
    return {queue: result, accepted: true}
}

// API-provided artwork must not cause local file reads or arbitrary tracking URLs.
function artUrl(value) {
    var url = String(value || "")
    return /^https:\/\/(i\.scdn\.co|mosaic\.scdn\.co|image-cdn-[a-z0-9-]+\.spotifycdn\.com)\//.test(url) ? url : ""
}

// Flatten nullable old/new Spotify list wrappers into one presentational contract.
function itemView(entry) {
    if (!entry) return null
    var item = entry.uri ? entry : (entry.track || entry.item || entry.album || entry)
    if (!item || !item.uri || !item.name) return null
    var artists = (item.artists || []).map(function(artist) { return artist.name }).join(", ")
    var images = item.images || (item.album || {}).images || []
    return {uri: item.uri, type: item.type || String(item.uri).split(":")[1], title: item.name,
        subtitle: artists || (item.owner || {}).display_name || item.type || "Spotify",
        art: images.length ? artUrl(images[0].url) : "", playable: item.is_playable !== false && !item.is_local}
}

// Derive a next offset/cursor only from Spotify-hosted pagination metadata. Never
// follow a server-provided URL; the helper reconstructs endpoints from arguments.
function page(data, kind) {
    data = data || {}
    if (kind && data[kind + "s"]) data = data[kind + "s"]
    var items = (data.items || data.queue || []).map(itemView).filter(function(item) { return item !== null })
    var next = typeof data.next === "string" && /^https:\/\/api\.spotify\.com\/v1\//.test(data.next)
    return {items: items, nextOffset: next && typeof data.offset === "number" && typeof data.limit === "number"
        ? data.offset + data.limit : -1, before: next ? String((data.cursors || {}).before || "") : ""}
}

// Local MPRIS becomes authoritative only after the API confirms the local device.
// A remote device always uses API metadata and transport, even if local MPRIS lingers.
// An acknowledged transfer can select an empty device before it has playback data.
// That permits starting its first track, but must not invent playing/MPRIS state.
function playbackView(api, local, localAvailable, localName, selectedDevice) {
    api = api || {}
    var device = api.device || selectedDevice || {}
    var localDevice = !!device.id && device.name === localName
    var useLocal = !!api.device && localDevice && localAvailable && local !== null
    var item = api.item || {}
    var album = item.album || {}
    var images = album.images || item.images || []
    var available = !!device.id && !device.is_restricted && (!localDevice || localAvailable)
    return {
        title: useLocal ? (local.trackTitle || "Ready to play") : (item.name || (device.id ? "Ready to play" : "Choose a device")),
        artist: useLocal ? (local.trackArtist || "") : (item.artists || []).map(function(a) { return a.name }).join(", "),
        album: useLocal ? (local.trackAlbum || "") : (album.name || ""),
        art: useLocal ? artUrl(local.trackArtUrl) : (images.length ? artUrl(images[0].url) : ""),
        uri: item.uri || "", duration: useLocal ? (local.length || 0) * 1000 : (item.duration_ms || 0),
        position: useLocal ? (local.position || 0) * 1000 : (api.progress_ms || 0),
        playing: useLocal ? local.isPlaying : !!api.is_playing,
        source: useLocal ? "local" : "api", deviceId: device.id || "", deviceName: device.name || "No active device",
        canControl: available, canVolume: available && device.supports_volume !== false,
        volume: device.volume_percent === undefined ? 0 : device.volume_percent,
        shuffle: !!api.shuffle_state, repeat: api.repeat_state || "off"
    }
}

// Preserve playback on errors. Authorization failures stop automatic API work;
// rate limits carry an absolute deadline so queued mutations can be discarded.
function failureState(error, authenticated, now) {
    return {stale: true, authenticated: error.kind === "auth" ? false : authenticated,
        retryAt: error.kind === "rate_limit" ? now + Math.max(1, error.retry_after || 30) * 1000 : 0,
        message: error.message || "Spotify request failed."}
}
