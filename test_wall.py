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
    leader = Viewer(options(listen=address, canvas=(2200, 700)))
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
    result.save('wall-preview.png')
    for window in (leader, left, right):
        window.close()
    print('PASS: two network viewports join pixel-for-pixel into one continuous rotated canvas')


if __name__ == '__main__':
    main()
