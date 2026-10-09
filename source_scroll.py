#!/usr/bin/env python3
"""Rotated, continuously scrolling source viewer. Python 3.10+."""
import argparse
from collections import deque
import fnmatch
import math
import os
from pathlib import Path
import queue
import random
import threading
import time

from pygments import lex
from pygments.lexers import get_lexer_for_filename, TextLexer
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QApplication, QWidget

EXCLUDED = {'.git', '.svn', '.hg', 'node_modules', '__pycache__', '.venv'}


def discover(root, patterns):
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        for name in sorted(names):
            path = Path(directory) / name
            if not path.is_symlink() and any(fnmatch.fnmatch(name, p) for p in patterns):
                yield path


def read_source(path, limit, style):
    with path.open('rb') as f:
        raw = f.read(limit + 1)
    if len(raw) > limit or b'\0' in raw:
        return None
    code = raw.decode('utf-8', errors='replace').expandtabs(8)
    if not code.strip():
        return None
    try:
        lexer = get_lexer_for_filename(path.name, stripnl=False, ensurenl=False)
    except ClassNotFound:
        lexer = TextLexer(stripnl=False, ensurenl=False)
    lines = [[]]
    for token, value in lex(code, lexer):
        color = style.style_for_token(token)['color'] or 'cdd6f4'
        parts = value.split('\n')
        for i, part in enumerate(parts):
            if i:
                lines.append([])
            if part:
                lines[-1].append((part, '#' + color))
    if lines and not lines[-1]:
        lines.pop()
    return lines


def producer(args, output, stop):
    style = get_style_by_name(args.theme)
    paths = list(discover(args.directory, args.include.split(',')))
    if args.shuffle:
        random.shuffle(paths)
    def send(item):
        while not stop.is_set():
            try:
                output.put(item, timeout=.2)
                return True
            except queue.Full:
                pass
        return False
    while not stop.is_set():
        count = 0
        for path in paths:
            if stop.is_set():
                return
            try:
                lines = read_source(path, args.max_file_bytes, style)
            except (OSError, ValueError) as exc:
                print(f'Skipping {path}: {exc}', flush=True)
                continue
            if lines:
                count += 1
                if not send((str(path.relative_to(args.directory)), lines)):
                    return
        if not count:
            send(('No readable source files matched; check --include and size limit.', []))
            return


def wrap_line(segments, columns, metrics=None):
    """Split styled text without losing characters or indentation."""
    result, row, used = [], [], 0
    for text, color in segments:
        while text:
            if metrics is None:
                n = min(columns - used, len(text))
            else:
                available = columns - used
                low, high = 0, len(text)
                while low < high:
                    middle = (low + high + 1) // 2
                    if metrics.horizontalAdvance(text[:middle]) <= available:
                        low = middle
                    else:
                        high = middle - 1
                n = low
                if n == 0 and row:
                    result.append(row)
                    row, used = [], 0
                    continue
                n = max(1, n)
            row.append((text[:n], color))
            text = text[n:]
            used += metrics.horizontalAdvance(row[-1][0]) if metrics else n
            if used >= columns:
                result.append(row)
                row, used = [], 0
    if row or not result:
        result.append(row)
    return result


class Viewer(QWidget):
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.setWindowTitle('Source Scroll')
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.font = QFont(args.font)
        self.font.setStyleHint(QFont.StyleHint.Monospace)
        self.font.setPixelSize(args.font_size)
        self.font.setFixedPitch(True)
        self.metrics = QFontMetricsF(self.font)
        self.line_height = math.ceil(self.metrics.height() * args.line_spacing)
        self.margin = args.margin
        self.gutter_width = math.ceil(self.metrics.horizontalAdvance('9999999'))
        self.code_x = self.margin + self.gutter_width + self.margin
        self.cell = self.metrics.horizontalAdvance('M')
        self.rows = deque()
        self.pending = deque()
        self.offset = 0.0
        self.paused = False
        self.speed = args.speed
        self.output = queue.Queue(maxsize=2)
        self.stop = threading.Event()
        threading.Thread(target=producer, args=(args, self.output, self.stop), daemon=True).start()
        self.last = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(max(1, round(1000 / args.fps)))

    def dimensions(self):
        return (self.height(), self.width()) if self.args.rotation in (-90, 90) else (self.width(), self.height())

    def fill(self):
        width, height = self.dimensions()
        columns = max(self.cell, width - self.code_x - self.margin)
        target = math.ceil(height / self.line_height) + 4
        while len(self.rows) < target:
            if not self.pending:
                try:
                    name, lines = self.output.get_nowait()
                except queue.Empty:
                    break
                self.pending.append(('', [(f'── {name} ──', '#89b4fa')]))
                self.pending.extend((str(i), line) for i, line in enumerate(lines, 1))
                self.pending.extend([('', []), ('', [])])
            number, segments = self.pending.popleft()
            for i, row in enumerate(wrap_line(segments, columns, self.metrics)):
                self.rows.append((number if i == 0 else '·', row))

    def tick(self):
        now = time.monotonic()
        dt, self.last = min(now - self.last, .1), now
        self.fill()
        if not self.paused and len(self.rows) > math.ceil(self.dimensions()[1] / self.line_height) + 1:
            self.offset += self.speed * dt
            while self.offset >= self.line_height and self.rows:
                self.offset -= self.line_height
                self.rows.popleft()
            self.fill()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#0b0e14'))
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.args.rotation == -90:
            p.translate(0, self.height())
            p.rotate(-90)
        elif self.args.rotation == 90:
            p.translate(self.width(), 0)
            p.rotate(90)
        elif self.args.rotation == 180:
            p.translate(self.width(), self.height())
            p.rotate(180)
        width, height = self.dimensions()
        p.setFont(self.font)
        p.setClipRect(QRectF(self.margin, self.margin, max(1, width - 2 * self.margin), max(1, height - 2 * self.margin)))
        y = self.margin + self.metrics.ascent() - self.offset
        if not self.rows:
            p.setPen(QColor('#89b4fa'))
            p.drawText(QPointF(24, y), 'Scanning source directory…')
        for number, segments in self.rows:
            if y > height + self.line_height:
                break
            p.setPen(QColor('#596579'))
            number_x = self.margin + self.gutter_width - self.metrics.horizontalAdvance(number)
            p.drawText(QPointF(number_x, y), number)
            x = self.code_x
            p.save()
            p.setClipRect(QRectF(self.code_x, self.margin, max(1, width - self.code_x - self.margin), max(1, height - 2 * self.margin)), Qt.ClipOperation.IntersectClip)
            for text, color in segments:
                p.setPen(QColor(color))
                p.drawText(QPointF(x, y), text)
                x += self.metrics.horizontalAdvance(text)
            p.restore()
            y += self.line_height
        p.end()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.close()
        elif key == Qt.Key.Key_Space:
            self.paused = not self.paused
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Up):
            self.speed = min(2000, self.speed * 1.25)
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Down):
            self.speed = max(1, self.speed / 1.25)
        elif key == Qt.Key.Key_F:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.stop.set()
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--speed', type=float, default=45, help='Logical pixels per second (default: 45)')
    parser.add_argument('--font-size', type=int, default=20, help='Font size in logical pixels')
    parser.add_argument('--margin', type=int, default=24, help='Viewport and gutter padding in pixels')
    parser.add_argument('--line-spacing', type=float, default=1.2, help='Line-height multiplier')
    parser.add_argument('--font', default='DejaVu Sans Mono')
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--rotation', type=int, choices=[-90, 0, 90, 180], default=-90)
    parser.add_argument('--screen', type=int, default=0, help='Zero-based Qt screen index')
    parser.add_argument('--windowed', action='store_true')
    parser.add_argument('--shuffle', action='store_true')
    parser.add_argument('--theme', default='monokai', help='Pygments style name')
    parser.add_argument('--include', default='*.c,*.h,*.cpp,*.cc,*.hpp,*.rs,*.py,*.pl,*.pm,*.js,*.ts,*.go,*.java,*.sh,*.S,*.s,*.asm,*.dts,*.dtsi,Makefile,Kconfig,*.mk')
    parser.add_argument('--max-file-bytes', type=int, default=2 * 1024 * 1024)
    args = parser.parse_args()
    args.directory = args.directory.expanduser().resolve()
    if not args.directory.is_dir():
        parser.error('directory must be an existing directory')
    if args.margin < 0 or not math.isfinite(args.line_spacing) or not 1 <= args.line_spacing <= 3:
        parser.error('margin must be nonnegative and line-spacing must be between 1 and 3')
    if not math.isfinite(args.speed) or args.speed <= 0 or args.font_size < 6 or not 1 <= args.fps <= 240 or args.max_file_bytes < 1:
        parser.error('speed must be finite and positive, font-size >= 6, fps 1–240, max-file-bytes > 0')
    try:
        get_style_by_name(args.theme)
    except ClassNotFound:
        parser.error('unknown Pygments theme')
    app = QApplication([])
    if not 0 <= args.screen < len(app.screens()):
        parser.error(f'screen must be between 0 and {len(app.screens()) - 1}')
    viewer = Viewer(args)
    viewer.resize(1280, 720)
    viewer.winId()
    viewer.windowHandle().setScreen(app.screens()[args.screen])
    if args.windowed:
        viewer.show()
    else:
        viewer.showFullScreen()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
