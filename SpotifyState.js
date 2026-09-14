// Pure state transformations shared by the QML bridge and headless tests.

function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value) }
function string(value) { return typeof value === "string" }
function boolean(value) { return typeof value === "boolean" }
function number(value) { return typeof value === "number" && isFinite(value) && value >= 0 }
function integer(value) { return number(value) && Math.floor(value) === value }
function nullableString(value) { return value === null || string(value) }
function fields(value, schema) {
    return object(value) && Object.keys(schema).every(function(key) {
        return value[key] === undefined || schema[key](value[key])
    })
}
function images(value) {
    return Array.isArray(value) && value.every(function(image) { return object(image) && string(image.url) })
}
function artists(value) {
    return Array.isArray(value) && value.every(function(artist) { return object(artist) && string(artist.name) })
}
function metadata(value) {
    return fields(value, {uri: string, name: string, type: string, artists: artists, images: images,
        album: metadata, owner: function(owner) { return fields(owner, {display_name: nullableString}) },
        duration_ms: number, is_playable: boolean, is_local: boolean})
}
function validEntry(entry) {
    if (entry === null) return true // Spotify can omit unavailable library items.
    if (!object(entry)) return false
    var item = entry.uri ? entry : (entry.track !== undefined ? entry.track
        : entry.item !== undefined ? entry.item : entry.album !== undefined ? entry.album : entry)
    return item === null || (metadata(item) && string(item.uri) && string(item.name))
}
function validDevice(value) {
    return object(value) && nullableString(value.id) && string(value.name) && boolean(value.is_restricted) &&
        fields(value, {is_active: boolean, supports_volume: boolean,
            volume_percent: function(n) { return n === null || (integer(n) && n <= 100) }})
}
function validPlayback(value) {
    return value === null || (object(value) && (value.device === null || validDevice(value.device)) &&
        (value.item === null || (metadata(value.item) && string(value.item.uri) && string(value.item.name))) &&
        boolean(value.is_playing) && (value.progress_ms === null || number(value.progress_ms)) &&
        fields(value, {shuffle_state: boolean, repeat_state: function(s) { return ["off", "context", "track"].indexOf(s) >= 0 }}))
}
function validPage(value, queue) {
    return object(value) && Array.isArray(value[queue ? "queue" : "items"]) &&
        value[queue ? "queue" : "items"].every(validEntry) &&
        fields(value, {next: nullableString, offset: integer, limit: integer,
            cursors: function(c) { return c === null || fields(c, {before: nullableString, after: nullableString}) }})
}
function validData(command, data, args) {
    if (command === "status") return object(data) && boolean(data.configured) && boolean(data.authenticated) &&
        boolean(data.local_available) && string(data.device_name) && string(data.local_message)
    if (command === "configure") return object(data) && data.configured === true
    if (command === "login") return object(data) && data.authenticated === true
    if (command === "playback") return validPlayback(data)
    if (command === "devices") return object(data) && Array.isArray(data.devices) && data.devices.every(validDevice)
    if (command === "search") return object(data) && validPage(data[(args.kind || "track") + "s"], false)
    if (["library", "browse", "queue"].indexOf(command) >= 0) return validPage(data, command === "queue")
    return ["transfer", "play", "pause", "next", "previous", "seek", "shuffle", "repeat", "volume", "enqueue", "save"].indexOf(command) >= 0 && data === null
}
function invalidResult() {
    return {ok: false, error: {kind: "network", message: "Spotify helper returned an invalid response. Refresh when ready.", retry_after: 0}}
}
// Reject the whole response before touching QML state; never partially apply it.
function result(raw, command, args) {
    try {
        var value = JSON.parse(raw)
        if (!object(value) || typeof value.ok !== "boolean" || Object.keys(value).length !== 2) return invalidResult()
        if (value.ok) return Object.prototype.hasOwnProperty.call(value, "data") && validData(command, value.data, args || {}) ? value : invalidResult()
        var error = value.error
        if (!object(error) || !string(error.message) || !error.message ||
                ["auth", "setup", "storage", "network", "rate_limit", "input", "rejected", "no_device"].indexOf(error.kind) < 0 ||
                !number(error.retry_after) || error.retry_after > 2147483647 ||
                (error.kind === "rate_limit" && error.retry_after < 1) ||
                Object.keys(error).length !== 3) return invalidResult()
        return value
    } catch (_) { return invalidResult() }
}

// Each live widget owns its membership, not a shared last-writer-wins boolean.
function setOwner(owners, owner, open) {
    var result = owners.filter(function(candidate) { return candidate !== owner })
    if (open) result.push(owner)
    return result
}

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
    if (!entry || !validEntry(entry)) return null
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
    if (kind) data = data[kind + "s"]
    if (!validPage(data, object(data) && data.queue !== undefined)) return {items: [], nextOffset: -1, before: ""}
    var items = (data.items || data.queue).map(itemView).filter(function(item) { return item !== null })
    var next = typeof data.next === "string" && /^https:\/\/api\.spotify\.com\/v1\//.test(data.next)
    return {items: items, nextOffset: next && typeof data.offset === "number" && typeof data.limit === "number"
        ? data.offset + data.limit : -1, before: next ? String((data.cursors || {}).before || "") : ""}
}

// Spotify does not expose a verifiable Connect ID on spotifyd's MPRIS object.
// Names (even unique names) are not identities: use the Web API for all devices.
// An acknowledged transfer may select a device with no current playback.
function playbackView(api, selectedDevice) {
    api = api || {}
    var device = api.device || selectedDevice || {}
    var item = metadata(api.item) ? api.item : {}
    var album = item.album || {}
    var images = album.images || item.images || []
    var available = validDevice(device) && !!device.id && !device.is_restricted
    return {
        title: item.name || (device.id ? "Ready to play" : "Choose a device"),
        artist: (item.artists || []).map(function(a) { return a.name }).join(", "),
        album: album.name || "",
        art: images.length ? artUrl(images[0].url) : "",
        uri: item.uri || "", duration: item.duration_ms || 0,
        position: number(api.progress_ms) ? api.progress_ms : 0,
        playing: api.is_playing === true,
        source: "api", deviceId: device.id || "", deviceName: device.name || "No active device",
        canControl: available, canVolume: available && device.supports_volume !== false && device.volume_percent !== null,
        volume: device.volume_percent || 0,
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
