"""Prepared lookahead keeps scrolling while the producer is unavailable."""
import math
import tempfile
from pathlib import Path
from PySide6.QtWidgets import QApplication
from source_scroll import Viewer
from test_sync import options, pump


def position(viewer):
    return viewer.row_index * viewer.line_height + viewer.offset


def main():
    app = QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'fixture.c').write_text('int item(int value) { return value + 1; }\n' * 1000)
        viewer = Viewer(options(directory=root, include='*.c'))
        viewer.resize(1100, 700)
        viewer.show()
        viewer.paused = True
        for _ in range(100):
            pump(app, .025)
            if len(viewer.rows) >= math.ceil(viewer.dimensions()[1] / viewer.line_height) + 64:
                break
        visible = math.ceil(viewer.dimensions()[1] / viewer.line_height)
        assert len(viewer.rows) >= visible + 64, 'lookahead did not fill'
        assert viewer.text_cache, 'prepared glyph cache empty'
        viewer.stop.set()
        viewer.pending.clear()
        while not viewer.output.empty():
            viewer.output.get_nowait()
        # Do not allow producer output during this section.
        original_fill = viewer.fill
        viewer.fill = lambda: None
        viewer.paused = False
        start = position(viewer)
        pump(app, .5)
        assert not viewer.buffering
        assert 15 < position(viewer) - start < 35
        viewer.fill = original_fill
        viewer.close()
    print('PASS: 64-row prepared lookahead, populated text cache, continuous motion during producer starvation')


if __name__ == '__main__':
    main()
