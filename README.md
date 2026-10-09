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
