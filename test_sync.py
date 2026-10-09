"""Run with QT_QPA_PLATFORM=offscreen python test_sync.py."""
from pathlib import Path
import json
import socket
import time
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication
from source_scroll import Viewer


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(.005)


def options(**changes):
    values = dict(directory=Path(__file__).parent.resolve(), include='*.py', shuffle=False,
                  theme='monokai', max_file_bytes=2000000, font='DejaVu Sans Mono',
                  font_size=24, line_spacing=1.2, margin=24, speed=45, fps=60,
                  rotation=-90, canvas=None, viewport=None, listen=None, connect=None, token='test')
    values.update(changes)
    return SimpleNamespace(**values)


def main():
    app = QApplication([])
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    address = f'127.0.0.1:{port}'
    leader = Viewer(options(listen=address))
    follower = Viewer(options(connect=address))
    second = Viewer(options(connect=address, rotation=0))
    for w in (leader, follower, second):
        w.resize(1100, 700)
        w.show()
    second.resize(800, 600)
    pump(app, 1.2)
    for client in (follower, second):
        assert client.remote_state and client.rows
        assert client.remote_dimensions == list(leader.dimensions())
        assert client.remote_state['rows'][0] == json.loads(json.dumps(list(leader.rows)[0]))
        assert abs((client.row_index - leader.row_index) * leader.line_height + client.offset - leader.offset) < 8
    leader.paused = True
    pump(app, .3)
    for client in (follower, second):
        assert client.paused
        assert abs((client.row_index - leader.row_index) * leader.line_height + client.offset - leader.offset) < .01
    leader.speed = 120
    leader.paused = False
    pump(app, .3)
    assert follower.speed == 120 and not follower.paused
    follower.grab().save('sync-preview.png')
    leader.close()
    pump(app, .4)
    leader = Viewer(options(listen=address))
    leader.resize(1100, 700)
    leader.show()
    pump(app, 1.8)
    assert follower.client.get()[1] == 'Following leader'
    assert abs((follower.row_index - leader.row_index) * leader.line_height + follower.offset - leader.offset) < 8
    for w in (leader, follower, second):
        w.close()
    print('PASS: two clients, differing viewports/rotations, position, pause, speed, leader restart and reconnect')


if __name__ == '__main__':
    main()
