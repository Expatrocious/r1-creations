# Pixel-art icon for the Wall St Pager R1 creation. Run: python3 tools/pager_icon.py pager/icon.png 8 (needs Pillow)
# Drawn on a 32x32 grid, then scaled up with hard edges; the LCD gets a dot-matrix gap and a phosphor glow.
import sys
from PIL import Image, ImageFilter, ImageChops

N = 36
OFF = 2   # the drawing below is on a 32 grid, centred on the 36 canvas
SCALE = int(sys.argv[2]) if len(sys.argv) > 2 else 8
OUT = sys.argv[1] if len(sys.argv) > 1 else "icon.png"

PAL = {
    ".": (14, 14, 16),      # icon background, same as the tasbih icon
    "_": (8, 8, 10),        # drop shadow under the pager
    "K": (4, 5, 6),         # outline
    "H": (78, 88, 96),      # body highlight
    "B": (44, 50, 56),      # body
    "S": (26, 30, 34),      # body shadow
    "D": (6, 10, 8),        # screen bezel
    "L": (13, 29, 18),      # lcd background (pager's --lcd-hi)
    "g": (24, 66, 40),      # dim phosphor for the chart fill
    "M": (52, 170, 96),     # mid phosphor, thickens the chart line
    "G": (93, 255, 148),    # phosphor green (pager's --ink)
    "W": (210, 255, 225),   # hot pixel at the live end of the line
    "R": (255, 59, 59),     # message LED
    "P": (255, 190, 190),   # LED highlight
    "n": (120, 130, 138),   # button highlight
    "b": (72, 80, 88),      # button
    "e": (120, 230, 160),   # select button highlight
    "E": (46, 150, 88),     # select button
}
LCD = set("LgMGW")

g = [["."] * N for _ in range(N)]
def px(x, y, c):
    x += OFF; y += OFF
    if 0 <= x < N and 0 <= y < N: g[y][x] = c
def hline(x0, x1, y, c):
    for x in range(x0, x1 + 1): px(x, y, c)
def rect(x0, y0, x1, y1, c):
    for y in range(y0, y1 + 1): hline(x0, x1, y, c)

# drop shadow
hline(5, 28, 30, "_"); hline(6, 27, 31, "_")

# belt clip peeking over the top
hline(7, 10, 3, "K"); px(6, 4, "K"); px(11, 4, "K"); px(6, 5, "K"); px(11, 5, "K")
hline(7, 10, 4, "H"); hline(7, 10, 5, "B")

# body, rounded corners
rect(3, 7, 28, 28, "B")
hline(4, 27, 6, "K"); hline(4, 27, 29, "K")
for y in range(8, 28): px(2, y, "K"); px(29, y, "K")
px(3, 7, "K"); px(28, 7, "K"); px(3, 28, "K"); px(28, 28, "K")
hline(4, 27, 7, "H")                                   # top edge catches the light
for y in range(8, 28): px(3, y, "H"); px(28, y, "S")   # left lit, right in shade
hline(4, 27, 28, "S"); hline(4, 27, 27, "S")           # bottom shade

# message LED, top right
px(25, 8, "P"); px(26, 8, "R")

# screen: bezel then lcd
rect(4, 9, 27, 22, "D")
rect(5, 10, 26, 21, "L")

# 3x5 font
FONT = {
    "S": ["GGG", "G..", "GGG", "..G", "GGG"],
    "P": ["GG.", "G.G", "GG.", "G..", "G.."],
    "Y": ["G.G", "G.G", ".G.", ".G.", ".G."],
}
def text(s, x, y):
    for ch in s:
        for dy, row in enumerate(FONT[ch]):
            for dx, c in enumerate(row):
                if c != ".": px(x + dx, y + dy, c)
        x += 4
text("SPY", 6, 11)

# up arrow
px(20, 12, "G"); hline(19, 21, 13, "G"); hline(18, 22, 14, "G")

# rising chart with a dithered fill underneath
ys = [20, 20, 19, 20, 20, 19, 18, 18, 19, 19, 18, 18, 18, 19, 18, 17, 17, 18, 17, 15, 14, 13]   # x = 5..26
for i, y in enumerate(ys):
    x = 5 + i
    for fy in range(y + 2, 22):
        if (x + fy) % 2 == 0: px(x, fy, "g")
    px(x, y + 1, "M")
    px(x, y, "G")
    if i and abs(ys[i - 1] - y) > 1:   # keep the line joined on steep steps
        for fy in range(min(ys[i - 1], y) + 1, max(ys[i - 1], y)): px(x, fy, "G")
px(26, 13, "W")

# three buttons with drop shadows
for x0 in (7, 14, 21):   # the middle one is the green select button
    sel = x0 == 14
    hline(x0, x0 + 3, 24, "e" if sel else "n"); hline(x0, x0 + 3, 25, "E" if sel else "b"); hline(x0 + 1, x0 + 4, 26, "S")

# ---------- render ----------
W = N * SCALE
img = Image.new("RGB", (W, W))
halo = Image.new("L", (W, W), 0)
halo.paste(255, (8 * SCALE, 11 * SCALE, 28 * SCALE, 25 * SCALE))
halo = halo.filter(ImageFilter.GaussianBlur(SCALE * 4))
glow = Image.new("RGB", (W, W), (0, 0, 0))
gap = max(1, SCALE // 8)
for y in range(N):
    for x in range(N):
        c = g[y][x]
        x0, y0 = x * SCALE, y * SCALE
        img.paste(PAL[c], (x0, y0, x0 + SCALE, y0 + SCALE))
        if c in LCD:
            # dot matrix: each lcd pixel sits in a slightly darker grid, like the pager's screen texture
            dark = tuple(int(v * 0.55) for v in PAL["L"])
            img.paste(dark, (x0 + SCALE - gap, y0, x0 + SCALE, y0 + SCALE))
            img.paste(dark, (x0, y0 + SCALE - gap, x0 + SCALE, y0 + SCALE))
        if c in "GW":
            glow.paste(PAL[c], (x0, y0, x0 + SCALE, y0 + SCALE))
        if c in "RP":
            glow.paste((255, 60, 60), (x0, y0, x0 + SCALE, y0 + SCALE))

# phosphor and LED glow, kept to the screen and the LED
glow = glow.filter(ImageFilter.GaussianBlur(SCALE * 0.9))
mask = Image.new("L", (W, W), 0)
mask.paste(255, ((5 + OFF) * SCALE, (10 + OFF) * SCALE, (27 + OFF) * SCALE, (22 + OFF) * SCALE))
mask.paste(255, ((23 + OFF) * SCALE, (7 + OFF) * SCALE, (29 + OFF) * SCALE, (10 + OFF) * SCALE))
glow = Image.composite(glow, Image.new("RGB", (W, W)), mask)
img = ImageChops.add(img, glow.point(lambda v: int(v * 0.55)))
bgmask = Image.new("L", (W, W), 0)   # only where the background shows
for y in range(N):
    for x in range(N):
        if g[y][x] in "._": bgmask.paste(255, (x * SCALE, y * SCALE, x * SCALE + SCALE, y * SCALE + SCALE))
tint = Image.merge("RGB", (halo.point(lambda v: int(v * 0.02)), halo.point(lambda v: int(v * 0.10)), halo.point(lambda v: int(v * 0.05))))
img = ImageChops.add(img, Image.composite(tint, Image.new("RGB", (W, W)), bgmask))

# a faint glass reflection across the top-left of the screen
ref = Image.new("L", (W, W), 0)
for i in range(SCALE * 5):
    for t in range(SCALE * 2):
        x, y = (5 + OFF) * SCALE + i + t, (10 + OFF) * SCALE + i // 2
        if x < (26 + OFF) * SCALE and y < (22 + OFF) * SCALE: ref.putpixel((x, y), 18)
img = ImageChops.add(img, Image.merge("RGB", (ref, ref, ref)))

img.save(OUT, optimize=True)
print("wrote", OUT, img.size)
