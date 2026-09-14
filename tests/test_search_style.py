#!/usr/bin/env python3
"""Run the real search controls in Qt without Spotify or a Wayland shell."""
from pathlib import Path
import os
import subprocess
import tempfile

source = (Path(__file__).resolve().parents[1] / "SpotifyPanel.qml").read_text()
action = source[source.index("    component Action: Button {"):source.index("    component SeekSlider: Slider {")]
start = source.rfind("            RowLayout {", 0, source.index("id: search\n"))
row = source[start:source.index("            // Search keeps", start)]
test = '''pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtTest
Item {
    id: panel
    width: 700; height: 300
    property int tab: 1
    property string searchKind: "track"
    property color green: "#1DB954"
    property string sans: "sans-serif"
    property QtObject palette: QtObject {
        property color text: "#eeeeee"
        property color muted: "#888888"
        property color border: "#444444"
        property color raised: "#333333"
        property color surface: "#222222"
    }
    property QtObject service: QtObject { property bool authenticated: true }
    function loadTab() {}
    QtObject { id: card; property color color: panel.palette.surface }
    Timer { id: searchDebounce; interval: 350 }
''' + action + '''
    Action { id: reference; text: "Library"; y: 200 }
    ColumnLayout {
        width: 660
''' + row + '''
    }
    TestCase {
        name: "SpotifySearchStyle"
        when: windowShown
        function test_external_action_labels_are_plain_text() {
            const original = reference.text
            reference.text = '<b>Device & title</b><img src="file:///not-read">'
            compare(reference.contentItem.textFormat, Text.PlainText)
            compare(reference.contentItem.text, reference.text)
            reference.text = original
        }
        function test_input_focus() {
            search.forceActiveFocus(Qt.OtherFocusReason)
            compare(search.background.border.color, panel.palette.border)
            compare(search.height, reference.height)
            compare(search.font.family, reference.font.family)
            reference.forceActiveFocus()
            keyClick(Qt.Key_Tab)
            compare(search.activeFocus, true)
            compare(search.background.border.color, panel.green)
            compare(search.background.border.width, 2)
        }
        function test_dropdown_states() {
            reference.forceActiveFocus()
            mouseMove(panel, 690, 290)
            compare(searchType.height, reference.height)
            compare(searchType.font.family, reference.font.family)
            compare(searchType.font.pixelSize, reference.font.pixelSize)
            compare(searchType.background.color, reference.background.color)
            compare(searchType.background.radius, reference.background.radius)
            compare(searchType.background.border.color, reference.background.border.color)
            mouseMove(searchType, searchType.width / 2, searchType.height / 2)
            tryCompare(searchType, "hovered", true)
            compare(searchType.background.color, panel.palette.raised)
            searchType.forceActiveFocus(Qt.TabFocusReason)
            compare(searchType.background.border.color, panel.green)
        }
        function test_keyboard_selection() {
            searchType.currentIndex = 0
            searchType.forceActiveFocus(Qt.TabFocusReason)
            keyClick(Qt.Key_Space)
            tryCompare(searchType.popup, "visible", true)
            keyClick(Qt.Key_Down)
            keyClick(Qt.Key_Return)
            compare(searchType.currentIndex, 1)
            compare(panel.searchKind, "album")
            tryCompare(searchType.popup, "visible", false)
        }
    }
}
'''
def main():
    with tempfile.TemporaryDirectory(prefix="oma-spotify-search-") as directory:
        path = Path(directory) / "tst_search.qml"
        path.write_text(test)
        runtime = Path(directory) / 'runtime'
        cache = Path(directory) / 'cache'
        runtime.mkdir(mode=0o700)
        cache.mkdir()
        return subprocess.call(
            ["/usr/lib/qt6/bin/qmltestrunner", "-input", str(path)],
            env={**os.environ, 'XDG_RUNTIME_DIR': str(runtime), 'XDG_CACHE_HOME': str(cache),
                 "QT_QPA_PLATFORM": "offscreen",
                 "QT_QUICK_BACKEND": "software", "QT_QUICK_CONTROLS_STYLE": "Basic"},
        )


if __name__ == '__main__':
    raise SystemExit(main())
