# Source Scroll

Native fullscreen source-code display for Linux/KDE/Wayland or X11. The entire canvas rotates 90 degrees counterclockwise by default, including line numbers and filenames. Vertical scrolling in the rotated coordinate system therefore travels leftward across the physical screen. Source files are read only.

## Install and run

Python 3.10 or newer:

```bash
unzip source-scroll.zip
cd source-scroll
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./run.sh /usr/src/linux-cachyos
```

CachyOS/Arch alternative, using system packages:

```bash
sudo pacman -S python-pyside6 python-pygments
python source_scroll.py /usr/src/linux-cachyos
```

## Adjust the display

```bash
./run.sh /usr/src/linux-cachyos --speed 70 --font-size 24 --screen 1
./run.sh /usr/src/linux-cachyos --shuffle --theme monokai
./run.sh /usr/src/linux-cachyos --include '*.c,*.h' --windowed
./run.sh /usr/src/linux-cachyos --rotation 0
```

Quote wildcard lists to prevent shell expansion. Screen indices follow Qt's enumeration. KDE may override initial screen placement; use its window rules if necessary. Fullscreen covers one chosen monitor.

Space pauses/resumes; +/− or up/down changes speed; F toggles fullscreen; Esc/Q exits.

## Behavior

Recursively scans the directory once, sorts paths, highlights each file by filename using Pygments, and repeats forever. `--shuffle` randomizes the initial playlist. Unknown file types use plain text. The default includes common programming languages, Linux assembly/device trees, Makefiles and Kconfig. Use `--include '*'` to include other text files.

Preserves indentation with eight-column tab expansion. Long lines wrap to the current viewport width; existing buffered rows adopt the new width as they scroll off after a resize. Highlighting is presentation formatting, not a source formatter. Files containing NUL bytes, empty files, symlinks, inaccessible files and files over 2 MiB are skipped. Increase `--max-file-bytes` if desired. Version-control directories and common dependency/cache directories are excluded.

A background worker reads and highlights files through a bounded queue. Only visible lines are painted; a monotonic clock drives fractional-pixel scrolling with an adjustable target frame rate (60 by default). Actual smoothness depends on the display compositor and system load. Buffer starvation pauses scrolling until the worker catches up. File contents can change between passes; the initial path list remains fixed.

Dependencies: Qt for Python/PySide6 and Pygments. See https://doc.qt.io/qtforpython-6/PySide6/QtGui/QPainter.html and https://pygments.org/docs/api/.

## Formatting controls

The number gutter is measured from the selected font, with a separate gap before code. Lines wrap by measured pixel width, and all edges have padding. Adjust with `--margin 32 --line-spacing 1.2`.

## Network wall: one canvas, multiple windows

Upgrade all machines to v5 for the buffering improvements. v4 and v5 use protocol 2; v3 is incompatible.

`--canvas WIDTHxHEIGHT` defines the complete wall **after rotation**, in wall pixels. `--viewport X,Y,W,H` selects the rectangle that this window displays. Coordinates have their origin at the wall's upper-left corner, X increases rightward and Y downward. Rotation happens once across the entire wall, before each window crops its own rectangle. There is no independent wrapping or gutter at monitor boundaries.

Example: three 1920×1080 screens side by side, total canvas 5760×1080:

```bash
# Left screen: also runs the leader
./run.sh /usr/src/linux-cachyos --listen 0.0.0.0:47770 \
  --canvas 5760x1080 --viewport 0,0,1920,1080 --screen 0

# Middle screen: on this or another machine
./run.sh --connect 192.168.1.10:47770 \
  --viewport 1920,0,1920,1080 --screen 0

# Right screen
./run.sh --connect 192.168.1.10:47770 \
  --viewport 3840,0,1920,1080 --screen 0
```

Replace the IP with the leader's reachable address. For windows on the same computer use `127.0.0.1:47770` and choose different `--screen` indices. Find indices with `./run.sh --list-screens`. `--view` is an alias for `--viewport`. Omit a viewport to preview the whole wall scaled down. The leader may show just its own viewport while streaming rows for the entire wall.

Example of unequal monitor sizes and offsets:

```bash
# Wall 7590x2400, using the coordinates of a configured display arrangement
./run.sh /usr/src/linux-cachyos --listen 0.0.0.0:47770 \
  --canvas 7590x2400 --viewport 0,240,3840,2160 --screen 0
./run.sh --connect 127.0.0.1:47770 --viewport 3840,0,1350,2400 --screen 1
./run.sh --connect 127.0.0.1:47770 --viewport 5190,540,2400,1350 --screen 2
```

These screen indices are examples: use Qt's actual enumeration. Use one consistent coordinate system across the wall. Qt normally reports logical pixels, which differ from physical resolution under fractional scaling. Physical wall pixel rectangles can also be used; each viewport scales uniformly to fit its local window. Matching viewport and local window aspect ratios prevents letterboxing. Gaps in the arrangement remain gaps in the virtual wall.

All clients receive the leader's highlighted rows, layout, rotation, font settings, pause and speed. Client directory, rotation, theme and font arguments do not override these shared settings. Clients keep local monitor selection and fullscreen controls. The leader controls pause/speed; those keys are ignored on clients. Install the same font on all machines for matching text rendering.

The source checkout is needed only on the leader. Shuffle and file transitions remain shared. Snapshot updates run at about 20 Hz and local rendering at the configured frame rate. Clients extrapolate between snapshots for at most 250 ms; disconnected clients freeze and reconnect. This is visual LAN synchronization, not frame-locked output.

### Connection troubleshooting

The leader prints its listening address. The client shows connection errors in an overlay and terminal, including refusal, timeout, token mismatch and incompatible handshake. Both sides must use the new version. Allow inbound **TCP 47770** on the leader. For a direct cable bind to the leader's actual cable address, e.g. `--listen 10.254.0.1:47770`, and connect to that address. `0.0.0.0` is a listen address, never a client destination. WSL networking can require Windows port forwarding or mirrored networking; first verify connectivity to the listening TCP port from the client machine.

Optional connection token: use the same `--token your-shared-token` on both sides. Transport is unencrypted TCP for trusted networks and transmits source text. Maximum 16 clients.

### Verification

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python test_sync.py
QT_QPA_PLATFORM=offscreen .venv/bin/python test_wall.py
```

The first tests two clients, pause/speed propagation and leader restart. The second stitches two client screenshots and verifies they equal the full rotated wall pixel-for-pixel.

## Smooth buffering (v5)

- Highlights up to eight upcoming files in a bounded background queue.
- Prepares at least 64 extra rows, or `--buffer-seconds` of lookahead at the current speed, whichever is greater.
- Gives preparation a 3 ms budget per tick, checked between source lines, to avoid draining large files in one GUI callback. A single unusually long source line can exceed the budget.
- Prepares and caches text fragments with Qt QStaticText; scroll frames reuse shaped text and its measured width.
- Skips rows outside the local viewport. The cache is bounded to 8192 text/font combinations.
- Uses low/high buffer thresholds so disk delays do not repeatedly stop and resume scrolling.
- Sends only the full wall's visible rows plus network lookahead, rather than the entire prepared buffer.
- Clients ease small position corrections over about 120 ms while retaining their local frame-rate animation. Pause snaps to the shared position, and leader session IDs reset interpolation after a restart.

Existing canvas and viewport commands continue to work. For more lookahead:

```bash
./run.sh /usr/src/linux-cachyos --listen 0.0.0.0:47770 \
  --canvas 7358x2075 --viewport 1366,525,2750,1550 \
  --buffer-seconds 12 --prefetch-files 12
```

These controls affect preparation on the leader; the default is six seconds and eight files. More file prefetch increases memory usage. Actual animation is still limited by the monitor refresh rate, compositor, network jitter and system load.

Buffer starvation check:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python test_buffer.py
```

This confirms the extra prepared rows and glyph cache exist, then stops supplying source rows and checks that buffered motion continues.
