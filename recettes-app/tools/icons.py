"""Génère l'icône (cadran de balance) et les écrans de démarrage Android."""
import math, os
from PIL import Image, ImageDraw

RES = os.path.join(os.path.dirname(__file__), '..', 'android', 'app', 'src', 'main', 'res')
ACCENT = (31, 122, 90)      # vert
CREAM = (247, 244, 238)     # fond de l'appli
WHITE = (255, 255, 255)
AMBER = (242, 170, 60)
SS = 4                      # suréchantillonnage pour l'anticrénelage


def dial(size, ring_d, color=WHITE, needle=AMBER):
    """Cadran centré sur une toile transparente de `size` px, anneau de diamètre `ring_d`."""
    S = size * SS
    im = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = S / 2
    R = ring_d * SS / 2
    w = max(2, round(R * 0.16))
    d.ellipse([c - R, c - R, c + R, c + R], outline=color, width=w)
    # graduations sur l'arc du haut
    for i in range(-4, 5):
        a = math.radians(-90 + i * 18)
        r1, r2 = R - w * 1.35, R - w * (2.35 if i % 2 == 0 else 1.95)
        tw = max(2, round(w * (0.42 if i % 2 == 0 else 0.3)))
        d.line([c + r1 * math.cos(a), c + r1 * math.sin(a), c + r2 * math.cos(a), c + r2 * math.sin(a)], fill=color, width=tw)
    # aiguille
    a = math.radians(-90 + 38)
    L = R - w * 2.1
    nw = max(3, round(w * 0.62))
    d.line([c, c, c + L * math.cos(a), c + L * math.sin(a)], fill=needle, width=nw)
    r = w * 0.75
    d.ellipse([c - r, c - r, c + r, c + r], fill=needle)
    # plateau
    pw, ph = R * 1.25, w * 0.9
    top = c + R + w * 1.0
    d.rounded_rectangle([c - pw / 2, top, c + pw / 2, top + ph], radius=ph / 2, fill=color)
    return im.resize((size, size), Image.LANCZOS)


def legacy(size, rounded=True):
    S = size * SS
    bg = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(bg)
    if rounded:
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=S * 0.22, fill=ACCENT)
    else:
        d.ellipse([0, 0, S - 1, S - 1], fill=ACCENT)
    bg = bg.resize((size, size), Image.LANCZOS)
    fg = dial(size, size * 0.52)
    # remonte un peu le cadran pour équilibrer le plateau
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.alpha_composite(bg)
    out.alpha_composite(fg, (0, -round(size * 0.04)))
    return out


DENS = {'mdpi': 1, 'hdpi': 1.5, 'xhdpi': 2, 'xxhdpi': 3, 'xxxhdpi': 4}
for name, k in DENS.items():
    folder = os.path.join(RES, f'mipmap-{name}')
    os.makedirs(folder, exist_ok=True)
    legacy(round(48 * k)).save(os.path.join(folder, 'ic_launcher.png'))
    legacy(round(48 * k), rounded=False).save(os.path.join(folder, 'ic_launcher_round.png'))
    fsz = round(108 * k)
    fg = Image.new('RGBA', (fsz, fsz), (0, 0, 0, 0))
    fg.alpha_composite(dial(fsz, fsz * 0.40), (0, -round(fsz * 0.03)))
    fg.save(os.path.join(folder, 'ic_launcher_foreground.png'))

with open(os.path.join(RES, 'values', 'ic_launcher_background.xml'), 'w') as f:
    f.write('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <color name="ic_launcher_background">#1F7A5A</color>\n</resources>\n')

# écrans de démarrage (anciens Android) : fond crème + icône au centre
for root, _, files in os.walk(RES):
    for fn in files:
        if fn == 'splash.png':
            p = os.path.join(root, fn)
            w, h = Image.open(p).size
            im = Image.new('RGBA', (w, h), CREAM + (255,))
            ic = legacy(round(min(w, h) * 0.26))
            im.alpha_composite(ic, ((w - ic.width) // 2, (h - ic.height) // 2))
            im.convert('RGB').save(p)

legacy(512).save(os.path.join(os.path.dirname(__file__), 'icon-512.png'))
print('ok')
