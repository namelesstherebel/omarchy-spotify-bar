pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Full plugin-owned player. Colors derive from the bar instead of the source
// theme, while every network and credential operation stays in the service.
Item {
    id: panel
    required property var service
    required property QtObject listState
    property bool active: false
    property var bar: null
    readonly property color green: "#1DB954"
    readonly property string sans: bar && bar.fontFamily ? bar.fontFamily : "sans-serif"
    readonly property color hostText: bar ? bar.foreground : "#eeeeee"
    property int tab: 0
    property string libraryKind: "tracks"
    property string searchKind: "track"

    property QtObject palette: QtObject {
        property color text: panel.hostText
        property color muted: Qt.rgba(panel.hostText.r, panel.hostText.g, panel.hostText.b, 0.62)
        property color border: Qt.rgba(panel.hostText.r, panel.hostText.g, panel.hostText.b, 0.25)
        property color raised: Qt.rgba(panel.hostText.r, panel.hostText.g, panel.hostText.b, 0.10)
        property color surface: bar && bar.background.a > 0 ? bar.background : "#181818"
    }

    // Format API milliseconds without allowing negative or non-finite output.
    function timeLabel(ms) {
        var seconds = Math.max(0, Math.floor((Number(ms) || 0) / 1000))
        return Math.floor(seconds / 60) + ":" + (seconds % 60 < 10 ? "0" : "") + seconds % 60
    }

    // Fetch only the visible list. Search debounce is cancelled on every tab change.
    function loadTab() {
        searchDebounce.stop()
        if (!active || !service || !service.authenticated) return
        if (tab === 0) service.load(listState, libraryKind, "", false)
        else if (tab === 1) {
            listState.searchKind = searchKind
            service.load(listState, "search", search.text, false)
        } else if (tab === 2) service.load(listState, "queue", "", false)
        else {
            service.cancelList(listState)
            listState.view = "devices"
            service.deviceGeneration++
            service.request("devices", {})
        }
    }

    onTabChanged: {
        loadTab()
        if (tab === 1) search.forceActiveFocus()
    }
    onActiveChanged: {
        searchDebounce.stop()
        if (active) Qt.callLater(loadTab)
    }
    Component.onCompleted: if (active) Qt.callLater(loadTab)

    Connections {
        target: panel.service
        function onAuthenticatedChanged() {
            if (panel.active && panel.service && panel.service.authenticated) panel.loadTab()
        }
    }
    Timer { id: searchDebounce; interval: 350; onTriggered: panel.loadTab() }

    component Action: Button {
        id: action
        property bool accent: false
        property bool selected: false
        implicitHeight: 34
        implicitWidth: Math.max(34, contentItem.implicitWidth + 24)
        hoverEnabled: true
        Accessible.name: text
        font.family: panel.sans
        font.pixelSize: 12
        background: Rectangle {
            radius: 9
            color: action.accent ? panel.green : action.selected || action.hovered ? panel.palette.raised : "transparent"
            border.width: action.visualFocus ? 2 : 1
            border.color: action.visualFocus ? panel.green : action.selected ? panel.palette.muted : panel.palette.border
            opacity: action.enabled ? 1 : 0.4
        }
        contentItem: Text { textFormat: Text.PlainText
            text: action.text
            font: action.font
            color: action.accent ? "#081c10" : panel.palette.text
            opacity: action.enabled ? 1 : 0.4
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }
    component SeekSlider: Slider {
        id: slider
        implicitHeight: 24
        Accessible.name: "Seek"
        signal committed(int target)
        property bool dirty: false
        property int pendingValue: 0
        // moved covers keyboard/wheel paths too. Defer until the input event
        // settles so moved + release cannot send the same action twice.
        onMoved: {
            dirty = true
            pendingValue = Math.round(value)
            if (!pressed) commitTimer.restart()
        }
        onPressedChanged: if (!pressed && dirty) commitTimer.restart()
        onEnabledChanged: if (!enabled) { dirty = false; commitTimer.stop() }
        Timer {
            id: commitTimer
            interval: 0
            onTriggered: {
                if (!slider.dirty || slider.pressed || !slider.enabled) return
                slider.dirty = false
                slider.committed(slider.pendingValue)
            }
        }
        background: Rectangle {
            x: slider.leftPadding
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            width: slider.availableWidth; height: 4; radius: 2
            color: panel.palette.raised
            Rectangle { width: slider.visualPosition * parent.width; height: parent.height; radius: 2; color: panel.green }
        }
        handle: Rectangle {
            x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            width: slider.visualFocus || slider.pressed ? 14 : 10; height: width; radius: width / 2
            color: slider.enabled ? panel.palette.text : panel.palette.muted
            border.width: slider.visualFocus ? 2 : 0; border.color: panel.green
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Image { source: "assets/spotify.svg"; Layout.preferredWidth: 28; Layout.preferredHeight: 28 }
            Text { textFormat: Text.PlainText; text: "Spotify"; color: panel.palette.text; font.pixelSize: 23; font.bold: true; font.family: panel.sans }
            Text { textFormat: Text.PlainText; text: "OMA SPOTIFY"; color: panel.palette.muted; font.pixelSize: 9; font.letterSpacing: 2; Layout.leftMargin: 8 }
            Item { Layout.fillWidth: true }
            Rectangle {
                Layout.preferredWidth: 7; Layout.preferredHeight: 7; radius: 4
                color: panel.service && panel.service.stale ? "#e5b567"
                    : panel.service && panel.service.authenticated ? panel.green : panel.palette.muted
            }
            Text { textFormat: Text.PlainText
                text: !panel.service ? "Starting" : panel.service.busy ? "Working…"
                    : panel.service.stale ? "Stale" : panel.service.authenticated ? "Connected" : "Not connected"
                color: panel.palette.muted; font.pixelSize: 11
            }
            Action {
                text: "↻"; Accessible.name: "Refresh Spotify"
                enabled: panel.service && !panel.service.busy
                onClicked: { panel.service.refresh(true); panel.loadTab() }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: statusText.implicitHeight + 18
            visible: panel.service && (panel.service.errorMessage !== "" || !panel.service.localAvailable)
            radius: 8
            color: Qt.rgba(panel.green.r, panel.green.g, panel.green.b, 0.08)
            Text { textFormat: Text.PlainText
                id: statusText
                anchors.fill: parent; anchors.margins: 9
                text: panel.service ? (panel.service.errorMessage || panel.service.localMessage) : ""
                color: panel.palette.muted; font.pixelSize: 11; wrapMode: Text.WordWrap
            }
        }

        ColumnLayout {
            visible: !panel.service || !panel.service.authenticated
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 14
            Item { Layout.fillHeight: true }
            Image {
                Layout.alignment: Qt.AlignHCenter; source: "assets/spotify.svg"
                sourceSize.width: 144; sourceSize.height: 144
                Layout.preferredWidth: 72; Layout.preferredHeight: 72
            }
            Text { textFormat: Text.PlainText
                Layout.alignment: Qt.AlignHCenter
                text: "Connect your Spotify account"
                color: panel.palette.text; font.pixelSize: 27; font.bold: true; font.family: panel.sans
            }
            Text { textFormat: Text.PlainText
                Layout.alignment: Qt.AlignHCenter; Layout.maximumWidth: 500
                text: "Paste only the Client ID from your Spotify Developer Dashboard. This field is stored as public data; format checks cannot distinguish an ID from a client secret. Never paste a secret. To correct a saved ID, replace it here and choose Save Client ID before connecting."
                horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                color: panel.palette.muted; font.pixelSize: 13; lineHeight: 1.3
            }
            TextField {
                id: clientId
                enabled: panel.service && !panel.service.busy
                Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 380
                placeholderText: "32-character Spotify Client ID"
                Accessible.name: "Spotify developer Client ID"
                selectByMouse: true
                color: panel.palette.text
                placeholderTextColor: panel.palette.muted
                maximumLength: 32
                validator: RegularExpressionValidator { regularExpression: /^[A-Fa-f0-9]{0,32}$/ }
                background: Rectangle {
                    radius: 9; color: panel.palette.raised
                    border.color: clientId.activeFocus ? panel.green : panel.palette.border; border.width: 2
                }
                onAccepted: if (text.length === 32 && panel.service && !panel.service.busy)
                    panel.service.request("configure", {client_id: text})
            }
            Action {
                Layout.alignment: Qt.AlignHCenter
                text: "Save Client ID"
                enabled: panel.service && !panel.service.busy && clientId.text.length === 32
                onClicked: panel.service.request("configure", {client_id: clientId.text})
            }
            Action {
                Layout.alignment: Qt.AlignHCenter
                text: panel.service && panel.service.current && panel.service.current.command === "login"
                    ? "Finish login in your browser…" : "Connect Spotify"
                accent: true
                enabled: panel.service && panel.service.configured && !panel.service.busy
                onClicked: panel.service.request("login", {})
            }
            Text { textFormat: Text.PlainText
                Layout.alignment: Qt.AlignHCenter
                text: "Spotify Premium required for playback controls · No client secret\nRedirect: http://127.0.0.1:8888/callback"
                horizontalAlignment: Text.AlignHCenter; color: panel.palette.muted; font.pixelSize: 10
            }
            Item { Layout.fillHeight: true }
        }

        RowLayout {
            visible: panel.service && panel.service.authenticated
            Layout.fillWidth: true
            spacing: 7
            Repeater {
                model: ["Library", "Search", "Queue", "Devices"]
                delegate: Action {
                    required property string modelData
                    required property int index
                    text: modelData
                    selected: panel.tab === index
                    onClicked: panel.tab = index
                }
            }
            Item { Layout.fillWidth: true }
            Text { textFormat: Text.PlainText
                text: panel.tab === 2 ? "Spotify supports add only. Queue reorder and clear are unavailable." : ""
                color: panel.palette.muted; font.pixelSize: 9
            }
        }

            RowLayout {
                visible: panel.service && panel.service.authenticated && panel.tab === 1
                Layout.fillWidth: true
                TextField {
                    id: search
                    Layout.fillWidth: true
                    implicitHeight: 34
                    leftPadding: 12
                    rightPadding: 12
                    font.family: panel.sans
                    font.pixelSize: 12
                    hoverEnabled: true
                    // Auto-focus does not look keyboard-selected. Tab focus does.
                    readonly property bool keyboardFocus: activeFocus && (focusReason === Qt.TabFocusReason || focusReason === Qt.BacktabFocusReason || focusReason === Qt.ShortcutFocusReason)
                    placeholderText: "What do you want to listen to?"
                    Accessible.name: "Search Spotify"
                    selectByMouse: true
                    color: panel.palette.text
                    placeholderTextColor: panel.palette.muted
                    background: Rectangle {
                        radius: 9
                        color: search.hovered ? panel.palette.raised : "transparent"
                        border.color: search.keyboardFocus ? panel.green : panel.palette.border
                        border.width: search.keyboardFocus ? 2 : 1
                    }
                    onTextEdited: searchDebounce.restart()
                    onAccepted: {
                        if (searchDebounce.running) { searchDebounce.stop(); panel.loadTab() }
                        else if (results.currentIndex >= 0) panel.service.playItem(panel.listState.items[results.currentIndex])
                    }
                    Keys.onDownPressed: { results.forceActiveFocus(); results.currentIndex = Math.min(results.count - 1, Math.max(0, results.currentIndex + 1)) }
                    Keys.onUpPressed: { results.forceActiveFocus(); results.currentIndex = Math.max(0, results.currentIndex - 1) }
                }
                ComboBox {
                    id: searchType
                    implicitHeight: 34
                    implicitWidth: 112
                    leftPadding: 12
                    rightPadding: 30
                    font.family: panel.sans
                    font.pixelSize: 12
                    hoverEnabled: true
                    model: ["Tracks", "Albums", "Artists", "Playlists"]
                    Accessible.name: "Search type"
                    onActivated: { panel.searchKind = ["track", "album", "artist", "playlist"][currentIndex]; panel.loadTab() }
                    background: Rectangle {
                        radius: 9
                        color: searchType.hovered || searchType.down ? panel.palette.raised : "transparent"
                        border.width: searchType.visualFocus ? 2 : 1
                        border.color: searchType.visualFocus ? panel.green : panel.palette.border
                    }
                    contentItem: Text { textFormat: Text.PlainText
                        text: searchType.displayText
                        font: searchType.font
                        color: panel.palette.text
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }
                    indicator: Text { textFormat: Text.PlainText
                        x: searchType.width - width - 12
                        y: (searchType.height - height) / 2
                        text: "⌄"
                        font: searchType.font
                        color: panel.palette.muted
                    }
                    delegate: ItemDelegate {
                        required property int index
                        required property string modelData
                        Accessible.name: modelData
                        width: searchType.popup.availableWidth
                        height: 34
                        highlighted: searchType.highlightedIndex === index
                        hoverEnabled: true
                        contentItem: Text { textFormat: Text.PlainText
                            text: modelData
                            font: searchType.font
                            color: panel.palette.text
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            radius: 6
                            color: parent.highlighted || parent.hovered ? panel.palette.raised : "transparent"
                        }
                    }
                    popup: Popup {
                        y: searchType.height + 4
                        width: searchType.width
                        padding: 4
                        implicitHeight: contentItem.implicitHeight + topPadding + bottomPadding
                        background: Rectangle {
                            radius: 9
                            color: panel.palette.surface
                            border.color: panel.palette.border
                        }
                        contentItem: ListView {
                            clip: true
                            implicitHeight: contentHeight
                            model: searchType.delegateModel
                            currentIndex: searchType.highlightedIndex
                            highlightMoveDuration: 0
                        }
                    }
                }
            }
            // Search keeps the current track available without taking space from results.
        RowLayout {
            visible: panel.service && panel.service.authenticated
            Layout.fillWidth: true
            Layout.preferredHeight: panel.tab === 1 ? 58 : 170
            spacing: 16
            Rectangle {
                visible: panel.tab !== 1
                Layout.preferredWidth: 160; Layout.preferredHeight: 160
                radius: 10; color: panel.palette.raised; clip: true
                Image {
                    anchors.centerIn: parent; source: "assets/spotify.svg"
                    width: 52; height: 52; opacity: 0.35
                    visible: cover.status !== Image.Ready
                }
                Image {
                    id: cover; anchors.fill: parent
                    source: panel.service ? panel.service.playback.art : ""
                    fillMode: Image.PreserveAspectCrop; asynchronous: true
                }
            }
            ColumnLayout {
                visible: panel.tab !== 1
                Layout.fillWidth: true
                spacing: 5
                Text { textFormat: Text.PlainText
                    text: panel.service && panel.service.playback.playing ? "NOW PLAYING" : "READY"
                    color: panel.green; font.pixelSize: 9; font.letterSpacing: 2
                }
                Text { textFormat: Text.PlainText
                    Layout.fillWidth: true
                    text: panel.service ? panel.service.playback.title : "Starting Spotify"
                    color: panel.palette.text; font.pixelSize: 23; font.bold: true
                    font.family: panel.sans; elide: Text.ElideRight
                }
                Text { textFormat: Text.PlainText
                    Layout.fillWidth: true; text: panel.service ? panel.service.playback.artist : ""
                    color: panel.palette.muted; font.pixelSize: 13; elide: Text.ElideRight
                }
                Text { textFormat: Text.PlainText
                    Layout.fillWidth: true; text: panel.service ? panel.service.playback.album : ""
                    color: panel.palette.muted; font.pixelSize: 11; elide: Text.ElideRight
                }
                SeekSlider {
                    id: seek
                    Layout.fillWidth: true
                    from: 0; to: Math.max(1, panel.service ? panel.service.playback.duration : 0)
                    value: panel.service ? panel.service.position : 0
                    enabled: panel.service && panel.service.playback.canControl && !panel.service.stale
                        && !panel.service.busy && panel.service.playback.duration > 0
                    stepSize: 1000
                    onCommitted: target => { if (panel.service) panel.service.transport("seek", {value: target}) }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text { textFormat: Text.PlainText; text: panel.timeLabel(seek.value); color: panel.palette.muted; font.pixelSize: 10 }
                    Item { Layout.fillWidth: true }
                    Text { textFormat: Text.PlainText; text: panel.timeLabel(panel.service ? panel.service.playback.duration : 0); color: panel.palette.muted; font.pixelSize: 10 }
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter; spacing: 8
                    enabled: panel.service && panel.service.playback.canControl && !panel.service.stale && !panel.service.busy
                    Action {
                        text: "Shuffle"; selected: panel.service && panel.service.playback.shuffle
                        onClicked: panel.service.transport("shuffle", {value: !panel.service.playback.shuffle})
                    }
                    Action { text: "◀|"; Accessible.name: "Previous track"; onClicked: panel.service.transport("previous", {}) }
                    Action {
                        text: panel.service && panel.service.playback.playing ? "Pause" : "Play"; accent: true
                        onClicked: panel.service.transport(panel.service.playback.playing ? "pause" : "play", {})
                    }
                    Action { text: "|▶"; Accessible.name: "Next track"; onClicked: panel.service.transport("next", {}) }
                    Action {
                        text: panel.service && panel.service.playback.repeat === "track" ? "Repeat 1" : "Repeat"
                        selected: panel.service && panel.service.playback.repeat !== "off"
                        onClicked: panel.service.transport("repeat", {value: panel.service.playback.repeat === "off"
                            ? "context" : panel.service.playback.repeat === "context" ? "track" : "off"})
                    }
                }
            }
            RowLayout {
                visible: panel.tab === 1
                Layout.fillWidth: true
                Rectangle {
                    Layout.preferredWidth: 48; Layout.preferredHeight: 48
                    radius: 8; color: panel.palette.raised; clip: true
                    Image { anchors.fill: parent; source: panel.service ? panel.service.playback.art : ""; fillMode: Image.PreserveAspectCrop }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    Text { textFormat: Text.PlainText; Layout.fillWidth: true; text: panel.service ? panel.service.playback.title : ""; color: panel.palette.text; font.bold: true; elide: Text.ElideRight }
                    Text { textFormat: Text.PlainText; Layout.fillWidth: true; text: panel.service ? panel.service.playback.artist : ""; color: panel.palette.muted; elide: Text.ElideRight }
                }
                Action {
                    text: panel.service && panel.service.playback.playing ? "Pause" : "Play"; accent: true
                    enabled: panel.service && panel.service.playback.canControl && !panel.service.busy
                    onClicked: panel.service.transport(panel.service.playback.playing ? "pause" : "play", {})
                }
            }
        }

        RowLayout {
            visible: panel.service && panel.service.authenticated
            Layout.fillWidth: true
            Action {
                text: "Device: " + (panel.service ? panel.service.playback.deviceName : "None")
                selected: panel.tab === 3 || (panel.service && !panel.service.playback.canControl)
                onClicked: panel.tab = 3
            }
            Item { Layout.fillWidth: true }
            Text { textFormat: Text.PlainText; text: "Volume"; color: panel.palette.muted; font.pixelSize: 10 }
            SeekSlider {
                id: volume
                Accessible.name: "Playback volume"
                Layout.preferredWidth: 120
                from: 0; to: 100; stepSize: 1
                value: panel.service ? panel.service.playback.volume : 0
                enabled: panel.service && panel.service.playback.canVolume && !panel.service.stale && !panel.service.busy
                onCommitted: target => { if (panel.service) panel.service.transport("volume", {value: target}) }
            }
            Text { textFormat: Text.PlainText; text: Math.round(volume.value) + "%"; color: panel.palette.muted; Layout.preferredWidth: 34 }
        }

        RowLayout {
            visible: panel.service && panel.service.authenticated && panel.tab === 0
            spacing: 6
            Repeater {
                model: [{key:"tracks",name:"Liked songs"},{key:"albums",name:"Albums"},
                    {key:"playlists",name:"Playlists"},{key:"recent",name:"Recent"}]
                delegate: Action {
                    required property var modelData
                    text: modelData.name
                    selected: panel.libraryKind === modelData.key && panel.listState.view !== "browse"
                    onClicked: { panel.libraryKind = modelData.key; panel.loadTab() }
                }
            }
        }

        Item {
            visible: panel.service && panel.service.authenticated
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: results
                anchors.fill: parent
                visible: panel.service && panel.tab !== 3
                clip: true; spacing: 4
                model: panel.listState.items
                currentIndex: count ? 0 : -1
                keyNavigationEnabled: true
                ScrollBar.vertical: ScrollBar { }
                Keys.onReturnPressed: if (currentIndex >= 0) panel.service.playItem(panel.listState.items[currentIndex])
                delegate: Rectangle {
                    id: resultRow
                    required property var modelData
                    required property int index
                    width: results.width; height: panel.tab === 1 ? 60 : 52; radius: 8
                    color: results.currentIndex === index ? panel.palette.raised : "transparent"
                    border.color: results.activeFocus && results.currentIndex === index ? panel.green : "transparent"
                    border.width: 2
                    MouseArea {
                        anchors.fill: parent
                        onClicked: { results.currentIndex = resultRow.index; results.forceActiveFocus() }
                        onDoubleClicked: panel.service.playItem(resultRow.modelData)
                    }
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 6; spacing: 9
                        Rectangle {
                            Layout.preferredWidth: 42; Layout.preferredHeight: 42; color: panel.palette.raised; radius: 5; clip: true
                            Image { anchors.fill: parent; source: resultRow.modelData.art; fillMode: Image.PreserveAspectCrop; asynchronous: true }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 1
                            Text { textFormat: Text.PlainText; Layout.fillWidth: true; text: resultRow.modelData.title; color: panel.palette.text; elide: Text.ElideRight }
                            Text { textFormat: Text.PlainText; Layout.fillWidth: true; text: resultRow.modelData.subtitle; color: panel.palette.muted; font.pixelSize: 10; elide: Text.ElideRight }
                        }
                        Action {
                            text: "Open"; visible: ["album", "artist", "playlist"].indexOf(resultRow.modelData.type) >= 0
                            onClicked: panel.service.browse(panel.listState, resultRow.modelData)
                        }
                        Action {
                            text: "+"; Accessible.name: "Add to queue"
                            visible: ["track", "episode"].indexOf(resultRow.modelData.type) >= 0
                            enabled: resultRow.modelData.playable && panel.service.playback.canControl
                                && !panel.service.stale && !panel.service.busy
                            onClicked: panel.service.enqueue(resultRow.modelData)
                        }
                        Action {
                            text: "♡"; Accessible.name: "Save to library"
                            visible: ["track", "album"].indexOf(resultRow.modelData.type) >= 0
                            enabled: !panel.service.busy
                            onClicked: panel.service.request("save", {uri: resultRow.modelData.uri})
                        }
                        Action {
                            text: "▶"; Accessible.name: "Play item"
                            enabled: resultRow.modelData.playable && panel.service.playback.canControl
                                && !panel.service.stale && !panel.service.busy
                            onClicked: panel.service.playItem(resultRow.modelData)
                        }
                    }
                }
                footer: Item {
                    width: results.width; height: more.visible ? 42 : 0
                    Action {
                        id: more; anchors.centerIn: parent; text: "Load more"
                        visible: panel.listState.nextOffset >= 0 || panel.listState.before !== ""
                        enabled: panel.service && !panel.service.busy
                        onClicked: panel.service.load(panel.listState, panel.listState.view, panel.listState.query, true)
                    }
                }
            }

            ListView {
                id: deviceList
                anchors.fill: parent
                visible: panel.service && panel.tab === 3
                clip: true; spacing: 6
                model: panel.service ? panel.service.devices : []
                ScrollBar.vertical: ScrollBar { }
                delegate: Action {
                    required property var modelData
                    width: deviceList.width; height: 48
                    text: modelData.name + (modelData.is_active ? " · Active" : "")
                    selected: modelData.is_active
                    enabled: !!modelData.id && !modelData.is_restricted && !panel.service.busy
                    onClicked: panel.service.request("transfer", {device_id: modelData.id})
                }
            }

            Column {
                anchors.centerIn: parent; width: parent.width - 40; spacing: 8
                visible: deviceList.visible ? deviceList.count === 0 : results.count === 0
                Text { textFormat: Text.PlainText
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: panel.service && panel.service.listBusy(panel.listState) ? "Finding your music…"
                        : deviceList.visible ? "Choose where the music plays"
                        : panel.tab === 1 ? "Search Spotify" : "Nothing here yet"
                    color: panel.palette.text; font.pixelSize: 18; font.family: panel.sans
                }
                Text { textFormat: Text.PlainText
                    width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                    text: deviceList.visible ? "Start spotifyd or open Spotify on another Connect device, then refresh."
                        : panel.tab === 1 ? "Search tracks, albums, artists, or playlists. Use ↑, ↓, and Enter."
                        : "Refresh this view or choose another library collection."
                    color: panel.palette.muted; font.pixelSize: 11
                }
            }
        }
    }
}
