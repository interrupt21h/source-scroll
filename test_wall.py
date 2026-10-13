"""Verify network viewport rendering reconstructs the full rotated wall."""
import socket
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from source_scroll import Viewer
from test_sync import options, pump


def main():
    app = QApplication([])
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    address = f'127.0.0.1:{port}'
    leader = Viewer(options(listen=address, canvas=(2200, 700), bottom_margin=100))
    left = Viewer(options(connect=address, viewport=(0, 0, 1100, 700)))
    right = Viewer(options(connect=address, viewport=(1100, 0, 1100, 700)))
    leader.resize(2200, 700)
    left.resize(1100, 700)
    right.resize(1100, 700)
    for window in (leader, left, right):
        window.show()
    pump(app, .8)
    leader.paused = True
    pump(app, .3)
    expected = leader.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    result = QImage(2200, 700, QImage.Format.Format_ARGB32)
    painter = QPainter(result)
    painter.drawImage(0, 0, left.grab().toImage())
    painter.drawImage(1100, 0, right.grab().toImage())
    painter.end()
    assert bytes(expected.constBits()) == bytes(result.constBits()), 'viewport composite differs from wall'
    for y in range(600, 700):
        for x in range(2200):
            assert result.pixelColor(x, y).name() == '#0b0e14', 'reserved bottom margin contains text'
    assert left.remote_state['bottom_margin'] == 100
    assert leader.dimensions() == (600, 2200)
    result.save('wall-preview.png')
    for window in (leader, left, right):
        window.close()
    print('PASS: network viewports join pixel-for-pixel, bottom margin stays blank and shared rotated layout shifts upward')


if __name__ == '__main__':
    main()
