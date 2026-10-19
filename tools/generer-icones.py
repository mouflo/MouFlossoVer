#!/usr/bin/env python3
"""Génère les icônes PNG de MouFlossover (même dessin que icons/mouflossover.svg). Usage : venv/bin/python tools/generer-icones.py"""
from pathlib import Path
from PIL import Image, ImageDraw

ICONES = Path(__file__).resolve().parent.parent / "icons"


def dessiner(size):
    S = size * 4
    k = S / 512
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    fond = Image.new("RGBA", (S, S))
    fd = ImageDraw.Draw(fond)
    for y in range(S):
        t = y / (S - 1)
        c = tuple(int(a + (b - a) * t) for a, b in zip((0x2a, 0x2d, 0x31), (0x12, 0x13, 0x15)))
        fd.line([(0, y), (S, y)], fill=c + (255,))
    masque = Image.new("L", (S, S), 0)
    ImageDraw.Draw(masque).rounded_rectangle([0, 0, S - 1, S - 1], radius=112 * k, fill=255)
    im.paste(fond, (0, 0), masque)

    def trait(points, largeur, couleur):
        w = largeur * k
        for a, b in zip(points, points[1:]):
            d.line([(a[0] * k, a[1] * k), (b[0] * k, b[1] * k)], fill=couleur, width=int(w))
        for p in points:
            r = w / 2
            d.ellipse([p[0] * k - r, p[1] * k - r, p[0] * k + r, p[1] * k + r], fill=couleur)

    vert = (0x6c, 0xcd, 0x65, 255)
    trait([(130, 130), (382, 382)], 46, vert)
    trait([(290, 382), (382, 382), (382, 290)], 46, vert)
    montante = ([(130, 382), (382, 130)], [(290, 130), (382, 130), (382, 222)])
    for pts in montante:
        trait(pts, 82, (0x12, 0x13, 0x15, 235))
    for pts in montante:
        trait(pts, 46, (0xf3, 0xf3, 0xf3, 255))
    return im.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    for nom, taille in (("icon-512.png", 512), ("icon-192.png", 192), ("apple-touch-icon.png", 180), ("favicon-32.png", 32)):
        dessiner(taille).save(ICONES / nom)
        print("✅", nom)
