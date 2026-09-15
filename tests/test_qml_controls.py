#!/usr/bin/env python3
"""Exercise real slider and popup-owner code offscreen, without host or network."""
from pathlib import Path
import json
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'SpotifyPanel.qml').read_text()
slider = source[source.index('    component SeekSlider: Slider {'):source.index('\n    ColumnLayout {')]

def instance(identity, end):
    start = source.rfind('SeekSlider {', 0, source.index('id: ' + identity + '\n'))
    block = source[start:source.index(end, start)].rstrip()
    # The next sibling starts at end; retain the real instance body.
    return block

seek = instance('seek', '                RowLayout {')
volume = instance('volume', '            Text { textFormat:')
widget = (root / 'BarWidget.qml').read_text()
start = widget.find('    property QtObject listState:')
owner_code = widget[start:widget.index('\n    visible:', start)] if start >= 0 else '''
    onPopupOpenChanged: if (spotify) spotify.panelOpen = popupOpen
    onSpotifyChanged: if (spotify) spotify.panelOpen = popupOpen
'''
# Real service state/methods with only the process launcher replaced. This also
# exercises QML property notifications that the headless JS tests cannot cover.
service_source = (root / 'Service.qml').read_text()
service_properties = service_source[service_source.index('    property var panelOwners:'):service_source.index('    function setPanelOpen(')]
service_methods = '\n'.join(match[0] for match in re.finditer(
    r'^    function (\w+)\([\s\S]*?^    }', service_source, re.M) if match[1] != 'pump')
qml = '''pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtTest
import "''' + root.as_uri() + '''/SpotifyState.js" as State
Item {
    id: panel
    width: 500; height: 300
    property color green: "#1DB954"
    property QtObject palette: QtObject {
        property color raised: "#333333"
        property color text: "#eeeeee"
        property color muted: "#888888"
    }
    property QtObject service: QtObject {
        property bool stale: false
        property bool busy: false
        property real position: 20000
        property var playback: ({duration: 100000, canControl: true, canVolume: true, volume: 50})
        property var calls: []
        function transport(command, args) { calls = calls.concat([{command: command, value: args.value}]) }
    }
''' + slider + '''
    ColumnLayout {
        width: 400
''' + seek + volume + '''
    }
    QtObject {
        id: shared
        property var owners: []
        property bool panelOpen: owners.length > 0
        function setPanelOpen(owner, open) { owners = State.setOwner(owners, owner, open) }
    }
    Component {
        id: widgetFactory
        Item {
            id: root
            property var spotify: shared
            property bool popupOpen: false
''' + owner_code + '''
        }
    }
    Item {
        id: listService
''' + service_properties + service_methods + '''
        property QtObject helper: QtObject {
            property bool running: true
            function signal(number) { }
        }
        property QtObject cleanupWatchdog: QtObject {
            function stop() { }
            function restart() { }
        }
        property QtObject watchdog: QtObject {
            function stop() { }
        }
        function pump() {
            if (current || pending.length === 0) return
            current = pending[0]; pending = pending.slice(1)
            helperTimedOut = false
        }
        function finish(title) {
            const page = {items:[{uri:"spotify:track:" + title, name:title}], offset:0, limit:2,
                next:"https://api.spotify.com/v1/me/tracks?offset=2"}
            const data = current.command === "search" ? {tracks:page} : page
            complete(JSON.stringify({ok:true,data:data}), 0)
        }
    }
    Item {
        id: pathService
''' + service_properties + re.search(r'^    function pump\([\s\S]*?^    }', service_source, re.M)[0] + '''
        property QtObject helper: QtObject {
            property var command: []
            property bool running: false
        }
        property QtObject watchdog: QtObject {
            property int interval: 0
            function restart() { }
        }
    }
    Component {
        id: listWidgetFactory
        Item {
            id: root
            width: 760; height: 850
            property var spotify: listService
            property bool popupOpen: false
            property alias player: player
''' + owner_code + '''
            TestSpotifyPanel {
                id: player
                anchors.fill: parent
                service: root.spotify
                listState: root.listState
                active: root.popupOpen
                visible: active
            }
        }
    }
    TestCase {
        name: "SpotifyControls"
        when: windowShown
        function init() { panel.service.calls = []; seek.value = 20000; volume.value = 50 }
        function test_real_qt_helper_url_and_literal_arguments() {
            const args = {query: "# %23 %25 % & ;"}
            pathService.pending = [{command:"search",args:args}]
            pathService.pump()
            compare(pathService.helper.command[1], EXPECTED_HELPER_PATH)
            compare(pathService.helper.command[2], "search")
            compare(JSON.parse(pathService.helper.command[3]), args)
        }
        function test_keyboard_data() { return [{tag: "seek", control: seek, command:"seek"}, {tag:"volume",control:volume,command:"volume"}] }
        function test_keyboard(data) {
            data.control.forceActiveFocus(Qt.TabFocusReason)
            keyClick(Qt.Key_Right)
            tryCompare(panel.service, "calls", [{command:data.command,value:Math.round(data.control.value)}])
            wait(30)
            compare(panel.service.calls.length, 1)
        }
        // Some Qt input paths emit moved without toggling pressed. Exercise that
        // documented signal contract as well as this platform's real key events.
        function test_unpressed_movement_data() { return test_keyboard_data() }
        function test_unpressed_movement(data) {
            compare(data.control.pressed, false)
            data.control.value += 1
            data.control.moved()
            tryCompare(panel.service, "calls", [{command:data.command,value:Math.round(data.control.value)}])
        }
        function test_no_change_gesture() {
            const x = volume.handle.x + volume.handle.width / 2
            mousePress(volume, x, volume.height / 2)
            mouseRelease(volume, x, volume.height / 2)
            wait(30)
            compare(panel.service.calls.length, 0)
        }
        function test_pointer_data() { return test_keyboard_data() }
        function test_pointer(data) {
            const c = data.control
            mousePress(c, c.width * 0.3, c.height / 2)
            mouseMove(c, c.width * 0.8, c.height / 2)
            compare(panel.service.calls.length, 0)
            mouseRelease(c, c.width * 0.8, c.height / 2)
            tryCompare(panel.service, "calls", [{command:data.command,value:Math.round(c.value)}])
            wait(30)
            compare(panel.service.calls.length, 1)
        }
        function test_programmatic_updates_do_not_commit() {
            seek.value = 50000; volume.value = 80
            wait(30)
            compare(panel.service.calls.length, 0)
        }
        function test_disabled_cancels_pending_commit_data() { return test_keyboard_data() }
        function test_disabled_cancels_pending_commit(data) {
            data.control.value += 1
            data.control.moved()
            panel.service.busy = true
            wait(30)
            compare(panel.service.calls.length, 0)
            panel.service.busy = false
            wait(30)
            compare(panel.service.calls.length, 0)
        }
        function test_multiple_owners_and_destruction() {
            const a = widgetFactory.createObject(panel)
            const b = widgetFactory.createObject(panel)
            a.popupOpen = true; b.popupOpen = true
            a.popupOpen = false
            compare(shared.panelOpen, true)
            a.destroy(); wait(1)
            compare(shared.panelOpen, true)
            b.destroy(); wait(1)
            compare(shared.panelOpen, false)
        }
        function action(parent, label) {
            if (parent.text === label && typeof parent.clicked === "function") return parent
            for (let child of parent.children || []) {
                const found = action(child, label)
                if (found) return found
            }
            return null
        }
        function test_correct_saved_client_id_without_connecting() {
            listService.authenticated = false
            listService.configured = true
            listService.current = null; listService.pending = []
            const widget = listWidgetFactory.createObject(panel)
            try {
                widget.popupOpen = true
                wait(1)
                const input = widget.player.clientControl
                compare(input.visible, true, "saved but unauthenticated ID remains editable")
                input.text = "b".repeat(32)
                const save = action(widget.player, "Save Client ID")
                const connect = action(widget.player, "Connect Spotify")
                verify(save !== null, "saving is independent of Connect")
                verify(connect !== null)
                compare(save.enabled, true)
                save.clicked()
                compare(listService.current.command, "configure")
                compare(listService.current.args.client_id, input.text)
                compare(save.enabled, false)
                compare(input.enabled, false)
                compare(connect.enabled, false)
                input.accepted() // Even a programmatic Enter cannot bypass busy.
                compare(listService.pending.length, 0)
                listService.current = null
                input.text = "c".repeat(32)
                input.accepted()
                compare(listService.current.command, "configure")
                compare(listService.current.args.client_id, input.text)
                listService.current = null
                connect.clicked()
                compare(listService.current.command, "login")
                compare(Object.keys(listService.current.args).length, 0)
            } finally {
                widget.destroy(); wait(1)
                listService.current = null; listService.pending = []
            }
        }
        function test_browsing_without_device_data() {
            return [{tag:"library", tab:0}, {tag:"search", tab:1}, {tag:"queue", tab:2}]
        }
        function test_browsing_without_device(data) {
            listService.authenticated = true
            listService.selectedDevice = null; listService.apiPlayback = null
            listService.current = null; listService.pending = []
            listService.stale = false
            const widget = listWidgetFactory.createObject(panel)
            try {
                widget.player.tab = data.tab
                widget.player.searchControl.text = "needle"
                widget.popupOpen = true
                wait(1)
                compare(listService.current.command, ["library", "search", "queue"][data.tab])
                const track = {uri:"spotify:track:Example", name:"Track"}
                const page = {items:[track], offset:0, limit:1, next:"https://api.spotify.com/v1/search?offset=1"}
                listService.complete(JSON.stringify({ok:true, data:data.tab === 2 ? {queue:[track]}
                    : data.tab === 1 ? {tracks:page} : page}), 0)
                const results = widget.player.resultControl
                compare(results.visible, true, "browsing must not require a playback device")
                compare(widget.player.deviceControl.visible, false)
                tryCompare(results, "count", 1)
                wait(1)
                const row = results.itemAtIndex(0)
                verify(row !== null)
                compare(action(row, "▶").enabled, false)
                compare(action(row, "+").enabled, false)
                compare(action(row, "♡").enabled, true)
                results.forceActiveFocus()
                keyClick(Qt.Key_Return)
                compare(listService.current, null, "Enter cannot play without a device")
                action(row, "♡").clicked()
                compare(listService.current.command, "save")
                listService.current = null
                if (data.tab !== 2) {
                    action(results.footerItem, "Load more").clicked()
                    compare(listService.current.args.offset, 1)
                    listService.current = null
                }
                widget.listState.items = [State.itemView({uri:"spotify:album:Example",name:"Album"})]
                wait(1)
                const open = action(results.itemAtIndex(0), "Open")
                compare(open.visible, true); compare(open.enabled, true)
                open.clicked()
                compare(listService.current.command, "browse")
                compare(listService.current.args.uri, "spotify:album:Example")
                listService.current = null
                widget.player.tab = 3
                compare(results.visible, false)
                compare(widget.player.deviceControl.visible, true)
            } finally {
                widget.destroy(); wait(1)
                listService.current = null; listService.pending = []
            }
        }
        function test_two_popup_lists_data() {
            return [{tag:"close-library", closeSearch:false, destroy:false},
                {tag:"close-search", closeSearch:true, destroy:false},
                {tag:"destroy-library", closeSearch:false, destroy:true},
                {tag:"destroy-search", closeSearch:true, destroy:true}]
        }
        function test_two_popup_lists(data) {
            listService.authenticated = true
            listService.selectedDevice = {id:"remote",name:"Remote",is_restricted:false}
            const a = listWidgetFactory.createObject(panel)
            const b = listWidgetFactory.createObject(panel)
            b.player.tab = 1
            b.player.searchControl.text = "needle"
            a.popupOpen = true; b.popupOpen = true
            wait(1)
            compare(listService.current.owner, a.listState)
            compare(listService.pending[0].owner, b.listState)
            listService.finish("Library")
            listService.finish("Search")
            compare(a.player.resultControl.count, 1)
            compare(b.player.resultControl.count, 1)
            compare(a.player.resultControl.model[0].title, "Library")
            compare(b.player.resultControl.model[0].title, "Search")
            // Exercise the actual Enter handlers, not just service.playItem.
            b.player.searchControl.forceActiveFocus()
            keyClick(Qt.Key_Return)
            compare(listService.current.command, "play")
            compare(listService.current.args.uri, "spotify:track:Search")
            listService.current = null
            a.player.resultControl.forceActiveFocus()
            keyClick(Qt.Key_Return)
            compare(listService.current.args.uri, "spotify:track:Library")
            listService.current = null
            const closed = data.closeSearch ? b : a
            const survivor = data.closeSearch ? a : b
            listService.load(closed.listState, closed.listState.view, closed.listState.query, true)
            listService.load(survivor.listState, survivor.listState.view, survivor.listState.query, true)
            if (data.destroy) { closed.destroy(); wait(1) }
            else closed.popupOpen = false
            compare(listService.panelOpen, true)
            compare(listService.current.cancelled, true)
            compare(listService.current.owner, null)
            listService.complete("late malformed output", 1)
            compare(listService.current.owner, survivor.listState)
            listService.finish("More")
            compare(survivor.player.resultControl.count, 2)
            compare(survivor.player.resultControl.model[1].title, "More")
            compare(listService.stale, false)
            if (!data.destroy) { closed.destroy(); wait(1) }
            survivor.destroy(); wait(1)
            compare(listService.panelOpen, false)
            compare(listService.pending.length, 0)
        }
        function test_service_replacement_releases_owner() {
            const a = widgetFactory.createObject(panel)
            a.popupOpen = true
            compare(shared.panelOpen, true)
            a.spotify = null
            compare(shared.panelOpen, false)
            a.destroy(); wait(1)
        }
    }
}
'''
def main():
    with tempfile.TemporaryDirectory(prefix='oma-controls-# %23 %25 %-') as directory:
        path = Path(directory) / 'tst_controls.qml'
        path.write_text(qml.replace('EXPECTED_HELPER_PATH', json.dumps(str(Path(directory) / 'core/spotifyctl.py'))))
        # Expose existing controls for keyboard testing; all handlers/bindings
        # remain the real panel source. Synthetic pages have no artwork URLs.
        (Path(directory) / 'TestSpotifyPanel.qml').write_text(source.replace(
            '    id: panel\n', '    id: panel\n    property alias resultControl: results\n    property alias searchControl: search\n    property alias clientControl: clientId\n    property alias deviceControl: deviceList\n').replace(
            '"assets/spotify.svg"', '"' + (root / 'assets/spotify.svg').as_uri() + '"'))
        runtime = Path(directory) / 'runtime'
        cache = Path(directory) / 'cache'
        runtime.mkdir(mode=0o700)
        cache.mkdir()
        return subprocess.call(['/usr/lib/qt6/bin/qmltestrunner', '-input', str(path)],
            env={**os.environ, 'XDG_RUNTIME_DIR': str(runtime), 'XDG_CACHE_HOME': str(cache),
                 'QT_QPA_PLATFORM':'offscreen', 'QT_QUICK_BACKEND':'software',
                 'QT_QUICK_CONTROLS_STYLE':'Basic'})


if __name__ == '__main__':
    raise SystemExit(main())
