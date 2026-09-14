#!/usr/bin/env python3
"""Exercise real slider and popup-owner code offscreen, without host or network."""
from pathlib import Path
import os
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
start = widget.find('    property var registeredService:')
owner_code = widget[start:widget.index('\n    visible:', start)] if start >= 0 else '''
    onPopupOpenChanged: if (spotify) spotify.panelOpen = popupOpen
    onSpotifyChanged: if (spotify) spotify.panelOpen = popupOpen
'''
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
    TestCase {
        name: "SpotifyControls"
        when: windowShown
        function init() { panel.service.calls = []; seek.value = 20000; volume.value = 50 }
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
    with tempfile.TemporaryDirectory(prefix='oma-controls-') as directory:
        path = Path(directory) / 'tst_controls.qml'
        path.write_text(qml)
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
