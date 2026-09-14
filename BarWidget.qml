import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui

// Compact Spotify control. The host injects a scoped bar API; this widget uses
// it only to find its own service and coordinate one popup per bar.
BarWidget {
    id: root
    moduleName: "blazeluminati.oma-spotify"

    readonly property var spotify: bar && bar.shell
        ? bar.shell.serviceFor("blazeluminati.oma-spotify") : null
    property bool popupOpen: false
    readonly property bool opened: popupOpen
    readonly property real openPanelIndicatorWidth: Math.min(implicitWidth, Style.space(90))

    // Open the full player and tell the service that list requests are useful.
    function open() { popupOpen = true }

    // Close the player and stop panel-only polling and list work.
    function close() { popupOpen = false }

    // Toggle the player for pointer and shell summon routes.
    function toggle() { popupOpen = !popupOpen }

    // Let another bar popup replace this one through the standard coordinator.
    function closeForPopoutSwitch() { close() }

    // Each popup owns its list, independently of shared playback and devices.
    property QtObject listState: QtObject {
        property var items: []
        property int nextOffset: -1
        property string before: ""
        property string view: "tracks"
        property string searchKind: "track"
        property string query: ""
        property string browseUri: ""
        property int listGeneration: 0
    }
    property var registeredService: null
    function syncPopupOwner() {
        if (registeredService && registeredService !== spotify)
            registeredService.setPanelOpen(listState, false)
        registeredService = spotify
        if (registeredService) registeredService.setPanelOpen(listState, popupOpen)
    }
    onPopupOpenChanged: syncPopupOwner()
    onSpotifyChanged: syncPopupOwner()
    Component.onCompleted: syncPopupOwner()
    Component.onDestruction: if (registeredService) registeredService.setPanelOpen(listState, false)

    visible: true
    implicitWidth: vertical ? barSize : controls.implicitWidth
    implicitHeight: vertical ? controls.implicitHeight : barSize

    Row {
        id: controls
        anchors.centerIn: parent
        spacing: Style.space(2)

        WidgetButton {
            id: opener
            bar: root.bar
            // Host label/tooltip formatting is outside this plugin's control.
            text: ""
            Accessible.name: "Open Spotify"
            fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
            fontSize: Style.font.body
            foreground: root.bar ? root.bar.barForeground : Color.foreground
            activeColor: "#1DB954"
            active: root.popupOpen
            fixedWidth: root.vertical ? root.barSize : Math.min(Style.space(190), Math.max(Style.space(82), implicitLabel.implicitWidth + Style.space(18)))
            tooltipText: "Open Spotify"
            onPressed: root.toggle()

            Text { textFormat: Text.PlainText
                id: implicitLabel
                anchors.centerIn: parent
                width: parent.width - Style.space(18)
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
                color: opener.foreground
                text: root.vertical ? "" : "  " + (root.spotify && root.spotify.authenticated
                    ? root.spotify.playback.title : "Spotify")
                font.family: opener.fontFamily
                font.pixelSize: opener.fontSize
            }
        }

        WidgetButton {
            visible: !root.vertical
            bar: root.bar
            text: root.spotify && root.spotify.playback.playing ? "Ⅱ" : "▶"
            fixedWidth: Style.space(28)
            foreground: root.spotify && root.spotify.playback.canControl ? "#1DB954"
                : (root.bar ? Qt.darker(root.bar.barForeground, 1.5) : Color.foreground)
            interactive: root.spotify && root.spotify.playback.canControl
                && !root.spotify.stale && !root.spotify.busy
            tooltipText: root.spotify && root.spotify.playback.playing ? "Pause" : "Play"
            onPressed: if (root.spotify) root.spotify.transport(
                root.spotify.playback.playing ? "pause" : "play", {})
        }

        WidgetButton {
            visible: !root.vertical
            bar: root.bar
            text: "▶|"
            fixedWidth: Style.space(28)
            interactive: root.spotify && root.spotify.playback.canControl
                && !root.spotify.stale && !root.spotify.busy
            tooltipText: "Next track"
            onPressed: if (root.spotify) root.spotify.transport("next", {})
        }
    }

    PopupCard {
        id: popup
        anchorItem: root
        bar: root.bar
        owner: root
        open: root.popupOpen
        contentWidth: fittedContentWidth(Style.space(760))
        contentHeight: fittedContentHeight(Style.space(650))

        SpotifyPanel {
            anchors.fill: parent
            service: root.spotify
            listState: root.listState
            active: root.popupOpen
            bar: root.bar
        }
    }
}
