#!/usr/bin/env python3
"""Rotated, continuously scrolling source viewer. Python 3.10+."""
import argparse
from collections import deque, OrderedDict
from itertools import islice
import fnmatch
import math
import os
from pathlib import Path
import queue
import random
import threading
import time
import uuid

from network_sync import SyncServer, SyncClient, endpoint
from wall_geometry import canvas_size, viewport_rect

from pygments import lex
from pygments.lexers import get_lexer_for_filename, TextLexer
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QStaticText, QTextOption
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
        self.row_index = 0
        self.paused = False
        self.speed = args.speed
        self.output = queue.Queue(maxsize=args.prefetch_files)
        self.stop = threading.Event()
        self.server = SyncServer(endpoint(args.listen), args.token) if args.listen else None
        self.client = SyncClient(endpoint(args.connect), args.token) if args.connect else None
        self.remote_dimensions = None
        self.remote_state = None
        self.last_publish = 0.0
        self.sync_status = ''
        self.session = uuid.uuid4().hex
        self.remote_session = None
        self.remote_position = None
        self.remote_font_key = None
        self.text_cache = OrderedDict()
        self.buffering = True
        if not self.client:
            threading.Thread(target=producer, args=(args, self.output, self.stop), daemon=True).start()
        self.last = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(max(1, round(1000 / args.fps)))

    def dimensions(self):
        if self.remote_dimensions:
            return self.remote_dimensions
        width, height = self.args.canvas or (self.width(), self.height())
        height = max(1, height - self.args.bottom_margin)
        return (height, width) if self.args.rotation in (-90, 90) else (width, height)

    def fill(self):
        width, height = self.dimensions()
        columns = max(self.cell, width - self.code_x - self.margin)
        target = math.ceil(height / self.line_height) + max(64, math.ceil(self.speed * self.args.buffer_seconds / self.line_height))
        deadline = time.perf_counter() + .003
        while len(self.rows) < target and time.perf_counter() < deadline:
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
                label = number if i == 0 else '·'
                self.rows.append((label, row))
                self.cached_text(label)
                for text, color in row:
                    self.cached_text(text)

    def tick(self):
        now = time.monotonic()
        dt, self.last = min(now - self.last, .1), now
        if self.client:
            latest, status = self.client.get()
            self.sync_status = status
            self.setWindowTitle('Source Scroll — ' + status)
            if latest:
                state, received, age = latest
                self.remote_state = state
                self.remote_dimensions = state['dimensions']
                font_key = (state['font'], state['font_size'])
                if font_key != self.remote_font_key:
                    self.remote_font_key = font_key
                    self.font.setFamily(state['font'])
                    self.font.setPixelSize(state['font_size'])
                    self.metrics = QFontMetricsF(self.font)
                    self.text_cache.clear()
                self.line_height = state['line_height']
                self.margin = state['margin']
                self.gutter_width = state['gutter_width']
                self.code_x = state['code_x']
                self.rows = deque(state['rows'])
                self.row_index = state['row_index']
                self.paused = state['paused']
                self.speed = state['speed']
                elapsed = min(.25, age + max(0, now - received))
                target_position = state['row_index'] * self.line_height + state['offset'] + (0 if state['paused'] else state['velocity'] * elapsed)
                new_session = state.get('session', '') != self.remote_session
                if self.remote_position is None or new_session or state['paused']:
                    self.remote_position = target_position
                    self.remote_session = state.get('session', '')
                else:
                    if age + max(0, now - received) < .25:
                        self.remote_position += state['velocity'] * dt
                    error = target_position - self.remote_position
                    if abs(error) > 2 * self.line_height:
                        self.remote_position = target_position
                    else:
                        self.remote_position += error * (1 - math.exp(-dt / .12))
                # Keep the interpolated cursor within the transmitted lookahead.
                self.remote_position = max(state['row_index'] * self.line_height, self.remote_position)
                self.offset = self.remote_position - state['row_index'] * self.line_height
                while self.offset >= self.line_height and len(self.rows) > 1:
                    self.offset -= self.line_height
                    self.rows.popleft()
                    self.row_index += 1
            self.update()
            return
        self.fill()
        visible = math.ceil(self.dimensions()[1] / self.line_height)
        if len(self.rows) <= visible + 1:
            self.buffering = True
        elif len(self.rows) >= visible + max(4, math.ceil(self.speed / self.line_height)):
            self.buffering = False
        if not self.paused and not self.buffering:
            self.offset += self.speed * dt
            while self.offset >= self.line_height and self.rows:
                self.offset -= self.line_height
                self.rows.popleft()
                self.row_index += 1
            self.fill()
        if self.server and now - self.last_publish >= .04:
            self.last_publish = now
            moving = not self.paused and not self.buffering
            send_count = visible + max(8, math.ceil(self.speed * .5 / self.line_height))
            self.server.publish({
                'time': now, 'session': self.session, 'rows': list(islice(self.rows, send_count)), 'offset': self.offset, 'row_index': self.row_index,
                'paused': self.paused, 'speed': self.speed,
                'velocity': self.speed if moving else 0,
                'dimensions': self.dimensions(), 'canvas': self.args.canvas or (self.width(), self.height()),
                'rotation': self.args.rotation, 'bottom_margin': self.args.bottom_margin, 'font': self.font.family(),
                'font_size': self.font.pixelSize(), 'line_height': self.line_height,
                'margin': self.margin, 'gutter_width': self.gutter_width, 'code_x': self.code_x,
            })
        self.update()

    def cached_text(self, text):
        key = (self.font.toString(), text)
        entry = self.text_cache.get(key)
        if entry is not None:
            self.text_cache.move_to_end(key)
            return entry
        static = QStaticText(text)
        static.setTextFormat(Qt.TextFormat.PlainText)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.NoWrap)
        static.setTextOption(option)
        static.prepare(font=self.font)
        entry = (static, self.metrics.horizontalAdvance(text))
        self.text_cache[key] = entry
        if len(self.text_cache) > 8192:
            self.text_cache.popitem(last=False)
        return entry

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#0b0e14'))
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.client and self.remote_state is None:
            p.setFont(QFont('Sans Serif', 14))
            p.setPen(QColor('#f9e2af'))
            p.drawText(QRectF(24, 24, self.width() - 48, self.height() - 48), Qt.TextFlag.TextWordWrap, self.client.get()[1])
            p.end()
            return
        rotation = self.remote_state['rotation'] if self.remote_state else self.args.rotation
        canvas = self.remote_state['canvas'] if self.remote_state else self.args.canvas or (self.width(), self.height())
        wall_width, wall_height = canvas
        bottom_margin = self.remote_state.get('bottom_margin', 0) if self.remote_state else self.args.bottom_margin
        content_height = max(1, wall_height - bottom_margin)
        vx, vy, vw, vh = self.args.viewport or (0, 0, wall_width, wall_height)
        if vx + vw > wall_width or vy + vh > wall_height:
            p.setPen(QColor('#f38ba8'))
            p.setFont(self.font)
            p.drawText(QRectF(24, 24, self.width() - 48, self.height() - 48),
                       Qt.TextFlag.TextWordWrap, f'Viewport {vx},{vy},{vw},{vh} exceeds canvas {wall_width}x{wall_height}.')
            p.end()
            return
        scale = min(self.width() / vw, self.height() / vh)
        p.save()
        p.translate((self.width() - vw * scale) / 2, (self.height() - vh * scale) / 2)
        p.setClipRect(QRectF(0, 0, vw * scale, vh * scale))
        p.scale(scale, scale)
        p.translate(-vx, -vy)
        p.setClipRect(QRectF(0, 0, wall_width, content_height), Qt.ClipOperation.IntersectClip)
        if rotation == -90:
            p.translate(0, content_height)
            p.rotate(-90)
        elif rotation == 90:
            p.translate(wall_width, 0)
            p.rotate(90)
        elif rotation == 180:
            p.translate(wall_width, content_height)
            p.rotate(180)
        width, height = self.dimensions()
        p.setFont(self.font)
        p.setClipRect(QRectF(self.margin, self.margin, max(1, width - 2 * self.margin), max(1, height - 2 * self.margin)), Qt.ClipOperation.IntersectClip)
        y = self.margin + self.metrics.ascent() - self.offset
        if not self.rows:
            p.setPen(QColor('#89b4fa'))
            p.drawText(QPointF(24, y), 'Waiting for leader…' if self.client else 'Scanning source directory…')
        clip = p.clipBoundingRect()
        for number, segments in self.rows:
            if y - self.metrics.ascent() > min(height + self.line_height, clip.bottom()):
                break
            if y + self.metrics.descent() < clip.top():
                y += self.line_height
                continue
            p.setPen(QColor('#596579'))
            number_text, number_width = self.cached_text(number)
            number_x = self.margin + self.gutter_width - number_width
            p.drawStaticText(QPointF(number_x, y - self.metrics.ascent()), number_text)
            x = self.code_x
            p.save()
            p.setClipRect(QRectF(self.code_x, self.margin, max(1, width - self.code_x - self.margin), max(1, height - 2 * self.margin)), Qt.ClipOperation.IntersectClip)
            for text, color in segments:
                p.setPen(QColor(color))
                static, advance = self.cached_text(text)
                p.drawStaticText(QPointF(x, y - self.metrics.ascent()), static)
                x += advance
            p.restore()
            y += self.line_height
        p.restore()
        if self.client:
            latest, status = self.client.get()
            stale = latest is None or time.monotonic() - latest[1] > .75
            if stale:
                p.fillRect(QRectF(0, 0, self.width(), 64), QColor('#151b28'))
                p.setFont(QFont('Sans Serif', 12))
                p.setPen(QColor('#f9e2af'))
                p.drawText(QRectF(16, 8, self.width() - 32, 48), Qt.TextFlag.TextWordWrap, status)
        p.end()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.close()
        elif self.client and key in (Qt.Key.Key_Space, Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Up, Qt.Key.Key_Minus, Qt.Key.Key_Down):
            return
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
        if self.client:
            self.client.close()
        if self.server:
            self.server.close()
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, nargs='?', help='Source directory; optional for clients')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--listen', metavar='HOST:PORT', help='Leader TCP bind address, e.g. 0.0.0.0:47770')
    mode.add_argument('--connect', metavar='HOST:PORT', help='Follow leader, e.g. 192.168.1.10:47770')
    parser.add_argument('--canvas', type=canvas_size, metavar='WIDTHxHEIGHT', help='Total wall size after rotation (leader)')
    parser.add_argument('--viewport', '--view', type=viewport_rect, metavar='X,Y,W,H', help='This window’s rectangle within the rotated wall')
    parser.add_argument('--list-screens', action='store_true', help='Print Qt screen indices and exit')
    parser.add_argument('--token', default='', help='Optional shared connection token (trusted LAN; no encryption)')
    parser.add_argument('--speed', type=float, default=45, help='Logical pixels per second (default: 45)')
    parser.add_argument('--font-size', type=int, default=20, help='Font size in logical pixels')
    parser.add_argument('--margin', type=int, default=24, help='Viewport and gutter padding in pixels')
    parser.add_argument('--bottom-margin', type=int, default=0, help='Reserve bottom space in wall units after rotation (leader setting)')
    parser.add_argument('--line-spacing', type=float, default=1.2, help='Line-height multiplier')
    parser.add_argument('--font', default='DejaVu Sans Mono')
    parser.add_argument('--buffer-seconds', type=float, default=6, help='Prepared text lookahead in seconds (minimum 64 rows)')
    parser.add_argument('--prefetch-files', type=int, default=8, help='Background highlighted-file queue size')
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--rotation', type=int, choices=[-90, 0, 90, 180], default=-90)
    parser.add_argument('--screen', type=int, default=0, help='Zero-based Qt screen index')
    parser.add_argument('--windowed', action='store_true')
    parser.add_argument('--shuffle', action='store_true')
    parser.add_argument('--theme', default='monokai', help='Pygments style name')
    parser.add_argument('--include', default='*.c,*.h,*.cpp,*.cc,*.hpp,*.rs,*.py,*.pl,*.pm,*.js,*.ts,*.go,*.java,*.sh,*.S,*.s,*.asm,*.dts,*.dtsi,Makefile,Kconfig,*.mk')
    parser.add_argument('--max-file-bytes', type=int, default=2 * 1024 * 1024)
    args = parser.parse_args()
    if args.listen or args.connect:
        try:
            endpoint(args.listen or args.connect)
        except ValueError as exc:
            parser.error(str(exc))
    if args.directory is None and not args.connect and not args.list_screens:
        parser.error('directory is required unless --connect is used')
    args.directory = (args.directory or Path('.')).expanduser().resolve()
    if not args.connect and not args.directory.is_dir():
        parser.error('directory must be an existing directory')
    if not math.isfinite(args.buffer_seconds) or not 1 <= args.buffer_seconds <= 60 or not 1 <= args.prefetch_files <= 64:
        parser.error('buffer-seconds must be 1–60 and prefetch-files 1–64')
    if args.bottom_margin < 0:
        parser.error('bottom-margin must be nonnegative')
    if args.canvas and args.bottom_margin >= args.canvas[1]:
        parser.error('bottom-margin must be smaller than canvas height')
    if args.margin < 0 or not math.isfinite(args.line_spacing) or not 1 <= args.line_spacing <= 3:
        parser.error('margin must be nonnegative and line-spacing must be between 1 and 3')
    if not math.isfinite(args.speed) or args.speed <= 0 or args.font_size < 6 or not 1 <= args.fps <= 240 or args.max_file_bytes < 1:
        parser.error('speed must be finite and positive, font-size >= 6, fps 1–240, max-file-bytes > 0')
    try:
        get_style_by_name(args.theme)
    except ClassNotFound:
        parser.error('unknown Pygments theme')
    app = QApplication([])
    if args.list_screens:
        for i, screen in enumerate(app.screens()):
            g = screen.geometry()
            print(f'{i}: {screen.name()} {g.width()}x{g.height()} at {g.x()},{g.y()} scale={screen.devicePixelRatio()}')
        return 0
    if args.viewport and args.canvas:
        x, y, w, h = args.viewport
        if x + w > args.canvas[0] or y + h > args.canvas[1]:
            parser.error('viewport exceeds canvas')
    if not 0 <= args.screen < len(app.screens()):
        parser.error(f'screen must be between 0 and {len(app.screens()) - 1}')
    try:
        viewer = Viewer(args)
    except OSError as exc:
        parser.error(f'Unable to start network listener: {exc}')
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
