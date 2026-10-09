"""Wall rectangles use final, rotated canvas coordinates, origin top left."""
def canvas_size(value):
    try:
        width, height = map(int, value.lower().split('x'))
    except (ValueError, AttributeError):
        raise ValueError('canvas must be WIDTHxHEIGHT') from None
    if min(width, height) <= 0 or max(width, height) > 32768:
        raise ValueError('canvas dimensions must be 1–32768')
    return width, height


def viewport_rect(value):
    try:
        x, y, width, height = map(int, value.split(','))
    except (ValueError, AttributeError):
        raise ValueError('viewport must be X,Y,WIDTH,HEIGHT') from None
    if min(x, y) < 0 or min(width, height) <= 0:
        raise ValueError('viewport origin must be nonnegative; size must be positive')
    return x, y, width, height
