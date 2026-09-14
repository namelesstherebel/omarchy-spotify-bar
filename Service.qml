pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import "SpotifyState.js" as State

// The host creates one service for the plugin. It owns API serialization and
// shared state for every monitor, and it never starts or configures spotifyd.
Item {
    id: bridge
    property var panelOwners: []
    readonly property bool panelOpen: panelOwners.length > 0
    property bool configured: false
    property bool authenticated: false
    property bool localAvailable: false
    property string localName: "Oma Spotify"
    property string localMessage: "Checking local audio…"
    property string errorMessage: ""
    property bool stale: false
    property bool suspended: false
    property double retryAt: 0
    property var apiPlayback: null
    property var selectedDevice: null
    property var devices: []
    property int deviceGeneration: 0
    property var pending: []
    property var current: null
    readonly property bool busy: current !== null
    property bool helperTimedOut: false
    readonly property var playback: State.playbackView(apiPlayback, selectedDevice)
    property double sampledAt: Date.now()
    property double now: Date.now()
    readonly property real position: Math.min(playback.duration, Math.max(0, playback.position +
        (playback.playing && !stale ? now - sampledAt : 0)))
    onPlaybackChanged: sampledAt = Date.now()

    function setPanelOpen(owner, open) {
        if (!owner) return
        if (!open) cancelList(owner)
        panelOwners = State.setOwner(panelOwners, owner, open)
    }

    // Only list reads belong to a popup. Detach its owner, but let the helper
    // finish under its existing deadlines so child cleanup and serialization hold.
    function cancelList(owner) {
        owner.listGeneration++
        pending = pending.filter(function(job) { return job.owner !== owner })
        if (current && current.owner === owner)
            current = Object.assign({}, current, {cancelled: true, owner: null})
    }

    function listBusy(owner) {
        return !!owner && ((current && current.owner === owner) ||
            pending.some(function(job) { return job.owner === owner }))
    }

    // Authorization/setup calls are allowed without a token; all other commands
    // wait for an authenticated account and a cleared retry deadline.
    function request(command, args, generation, append, owner) {
        var setup = ["status", "configure", "login"].indexOf(command) >= 0
        if (!setup && (!authenticated || Date.now() < retryAt)) return false
        var list = ["search", "library", "browse", "queue"].indexOf(command) >= 0
        if (list && (panelOwners.indexOf(owner) < 0 || generation !== owner.listGeneration)) return false
        if (!panelOpen && command === "devices") return false
        var result = State.enqueue(pending, current, {command: command, args: args || {},
            owner: list ? owner : null,
            generation: command === "devices" ? deviceGeneration : generation === undefined ? -1 : generation, append: !!append}, 8)
        pending = result.queue
        if (!result.accepted) errorMessage = "Too many pending actions. Wait for Spotify to respond."
        pump()
        return result.accepted
    }

    // Start one argument-list Process at a time. stdout is collected before the
    // exit signal, then parsed once; tokens are never part of this protocol.
    function pump() {
        if (current || pending.length === 0) return
        current = pending[0]
        pending = pending.slice(1)
        helper.command = ["python3", Qt.resolvedUrl("core/spotifyctl.py").toString().replace(/^file:\/\//, ""),
            current.command, JSON.stringify(current.args)]
        helperTimedOut = false
        helper.running = true
        watchdog.interval = current.command === "login" ? 215000 : 50000
        watchdog.restart()
    }

    // Kill once and stay busy until the child is reaped. Ignore all late output;
    // the timed-out action may already have happened and must never be replayed.
    function expire() {
        if (!current || helperTimedOut) return
        helperTimedOut = true
        pending = []
        if (helper.running) helper.signal(9)
        else complete("", -1) // A failed start need not emit an exit signal.
    }

    // Manual refresh clears network suspension, but cannot bypass auth or 429.
    function refresh(manual) {
        if (manual) { suspended = false; errorMessage = "" }
        if ((!enabled && !panelOpen) || (suspended && !manual) || Date.now() < retryAt) return
        request("status", {})
        if (authenticated) {
            request("playback", {})
            if (manual && panelOpen) request("devices", {})
        }
    }

    // Names cannot associate MPRIS with a Connect ID. All transport uses the
    // explicit API device ID, independent of local spotifyd availability.
    function transport(command, args) {
        if (!playback.canControl || stale || busy || Date.now() < retryAt) return
        args = args || {}
        args.device_id = playback.deviceId
        request(command, args)
    }

    // Replacing a view cancels only this owner's work. Pagination always uses
    // that same owner's query, kind, context and generation.
    function load(owner, nextView, text, append) {
        if (panelOwners.indexOf(owner) < 0 || !authenticated) return
        if (!append) {
            cancelList(owner)
            owner.view = nextView; owner.query = text || ""
            owner.items = []; owner.nextOffset = -1; owner.before = ""
        }
        var args = {offset: append ? owner.nextOffset : 0}
        if (owner.view === "search") {
            if (!owner.query.trim()) return
            args.query = owner.query; args.kind = owner.searchKind
            request("search", args, owner.listGeneration, append, owner)
        } else if (owner.view === "queue") {
            request("queue", {}, owner.listGeneration, false, owner)
        } else if (owner.view === "browse") {
            args.uri = owner.browseUri
            request("browse", args, owner.listGeneration, append, owner)
        } else {
            args.kind = owner.view
            if (owner.view === "recent") { delete args.offset; if (append) args.before = owner.before }
            request("library", args, owner.listGeneration, append, owner)
        }
    }

    // Track Enter starts that track; contexts start their album/artist/playlist.
    function playItem(item) {
        if (item && item.playable) transport("play", {uri: item.uri})
    }

    // Spotify supports append-only queue actions, not clear/reorder operations.
    function enqueue(item) {
        if (item && item.playable) transport("enqueue", {uri: item.uri})
    }

    // Open a context's contents on demand without starting playback.
    function browse(owner, item) {
        if (panelOwners.indexOf(owner) < 0 || !item) return
        owner.browseUri = item.uri
        load(owner, "browse", "", false)
    }

    // Apply one response, preserving stale playback on failure. Mutations queued
    // before an error are dropped, never replayed after reauth or a rate limit.
    function complete(raw, exitCode) {
        if (!current) return
        watchdog.stop()
        var job = current
        if (job.cancelled || (job.owner && (panelOwners.indexOf(job.owner) < 0 ||
                job.generation !== job.owner.listGeneration))) {
            current = null
            pump()
            return
        }
        var result = State.result(raw, job.command, job.args)
        if (helperTimedOut) result = {ok: false, error: {kind: "network",
            message: "Spotify helper timed out. An action may have completed; refresh before trying again.", retry_after: 0}}
        else if (result.ok && exitCode !== 0) result = State.invalidResult()
        current = null
        if (!result.ok) {
            var error = result.error || {}
            var failure = State.failureState(error, authenticated, Date.now())
            errorMessage = failure.message; stale = failure.stale
            authenticated = failure.authenticated; retryAt = failure.retryAt
            suspended = error.kind === "network" || error.kind === "storage"
            pending = []
            if (error.kind === "setup") configured = false
            // An explicit Spotify rejection gets one state refresh, not a retry
            // of the rejected mutation. Failed reads do not recurse.
            if (["rejected", "no_device"].indexOf(error.kind) >= 0 &&
                    ["playback", "status", "devices", "search", "library", "browse", "queue"].indexOf(job.command) < 0)
                request("playback", {})
        } else {
            var data = result.data
            if (job.command === "status") {
                configured = data.configured; authenticated = data.authenticated
                localAvailable = data.local_available; localName = data.device_name
                localMessage = data.local_message
            } else if (job.command === "configure" || job.command === "login") {
                configured = true; errorMessage = ""; stale = false; suspended = false
                if (job.command === "login") authenticated = true
                refresh(true)
            } else if (job.command === "playback") {
                apiPlayback = data; stale = false; sampledAt = Date.now()
                if (data && data.device) selectedDevice = null
            } else if (job.command === "devices") {
                if (panelOpen && job.generation === deviceGeneration) devices = (data || {}).devices || []
            } else if (["search", "library", "browse", "queue"].indexOf(job.command) >= 0) {
                if (job.owner && panelOwners.indexOf(job.owner) >= 0 && job.generation === job.owner.listGeneration) {
                    var page = State.page(data, job.command === "search" ? job.args.kind : "")
                    job.owner.items = job.append ? job.owner.items.concat(page.items) : page.items
                    job.owner.nextOffset = page.nextOffset; job.owner.before = page.before
                }
            } else {
                errorMessage = ""
                request("playback", {})
                if (job.command === "transfer") {
                    // Spotify may acknowledge a device with no current playback.
                    // Remember the accepted destination so search can start its
                    // first track; playback remains empty until Spotify reports it.
                    selectedDevice = devices.find(function(device) { return device.id === job.args.device_id }) || null
                    request("devices", {})
                }
                if (job.command === "enqueue") panelOwners.forEach(function(owner) {
                    if (owner.view === "queue") load(owner, "queue", "", false)
                })
            }
        }
        pump()
    }

    Process {
        id: helper
        stdout: StdioCollector { id: output }
        // stderr is deliberately not forwarded into the shell's logs.
        stderr: StdioCollector { }
        onExited: (exitCode, exitStatus) => bridge.complete(output.text, exitCode)
    }
    Timer {
        id: watchdog
        repeat: false
        onTriggered: bridge.expire()
    }
    Component.onDestruction: if (helper.running) helper.signal(9)
    Timer {
        interval: bridge.panelOpen ? 4000 : 15000
        running: bridge.enabled && !bridge.suspended
        repeat: true
        triggeredOnStart: true
        onTriggered: bridge.refresh(false)
    }
    Timer {
        interval: 500; repeat: true
        running: bridge.panelOpen
        onTriggered: {
            bridge.now = Date.now()
        }
    }
    onPanelOpenChanged: {
        if (panelOpen) {
            refresh(false)
            request("devices", {})
        } else {
            deviceGeneration++
            pending = pending.filter(function(job) { return job.command !== "devices" })
        }
    }
}
