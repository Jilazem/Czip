# -*- coding: utf-8 -*-
"""Repo kapak gorseli (1280x720 PNG) — terminal esteticili, neon yesil.

Kullanim:  python3 scripts/kapak.py [cikti.png]
Bagimlilik: Pillow (pip install pillow), Menlo fontu (macOS) veya bulunabilir font.
"""
import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
ZEMIN = (10, 12, 13)
PANEL = (16, 20, 22)
YESIL = (57, 255, 110)
CAMASIR = (0, 229, 255)
MACENTA = (255, 0, 200)
GRIMTI = (120, 140, 145)
BEYAZ = (235, 245, 240)
KIRMIZI = (255, 95, 95)
SARI = (250, 215, 90)


def font(pyv, kalin=False):
    yollar = [
        "/System/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    ]
    for y in yollar:
        try:
            return ImageFont.truetype(y, pyv)
        except OSError:
            continue
    return ImageFont.load_default()


def metin_golge(d, xy, metin, f, renk, glow=18):
    """Neon glow: ayni metni saydam katmanlarla tekrar cizip uste tam yazar.
    RGB modda alpha istenmedigi icin her katman ayri RGBA kanalinda degil,
    renk koyulastirilarak cizilir (glow sahtesi)."""
    x, y = xy
    for i in range(3, 0, -1):
        koyu = tuple(int(c * 0.25 * i) for c in renk)
        d.text((x - 2, y), metin, font=f, fill=koyu)
        d.text((x + 2, y), metin, font=f, fill=koyu)
        d.text((x, y - 2), metin, font=f, fill=koyu)
        d.text((x, y + 2), metin, font=f, fill=koyu)
    d.text((x, y), metin, font=f, fill=renk)


def ciz(cikti):
    im = Image.new("RGB", (W, H), ZEMIN)
    d = ImageDraw.Draw(im, "RGBA")

    # arka plan: dikey degerlik
    for yy in range(0, H, 2):
        k = 6 + int(8 * yy / H)
        d.line([(0, yy), (W, yy)], fill=(k, k + 1, k + 1))

    # scanline dokusu
    for yy in range(0, H, 3):
        d.line([(0, yy), (W, yy)], fill=(255, 255, 255, 5))

    # sol neon cizgi
    d.rectangle([0, 0, 6, H], fill=YESIL)
    d.rectangle([0, 0, 2, H], fill=(YESIL[0], YESIL[1], YESIL[2], 120))

    # terminal penceresi
    tx, ty, tw, th = 56, 56, 640, 500
    d.rounded_rectangle([tx, ty, tx + tw, ty + th], radius=14, fill=PANEL,
                        outline=(40, 60, 55), width=1)
    d.rounded_rectangle([tx, ty, tx + tw, ty + 44], radius=14, fill=(22, 28, 30))
    d.rectangle([tx, ty + 30, tx + tw, ty + 44], fill=(22, 28, 30))
    for i, c in enumerate((KIRMIZI, SARI, YESIL)):
        d.ellipse([tx + 18 + i * 26, ty + 14, tx + 34 + i * 26, ty + 30], fill=c)
    f_kucuk = font(19)
    d.text((tx + 110, ty + 14), "czip — session packer", font=f_kucuk, fill=GRIMTI)

    # terminal icerigi
    f_or = font(21)
    f_ir = font(28, True)
    satirlar = [
        ("$ ", "czip paketle son", YESIL, BEYAZ),
        ("", "PAKETLENDI  a37tvc", YESIL, YESIL),
        ("", "1117 ilet  4,2 MB → 237 KB   (18x)", GRIMTI, BEYAZ),
        ("", "paket acilmaz → sadece okunur", GRIMTI, CAMASIR),
        ("$ ", "czip oku a37tvc", YESIL, BEYAZ),
        ("", "INDEKS 0|user|outrun oyunu…", GRIMTI, GRIMTI),
        ("", "       3|assistant[terminal]", GRIMTI, GRIMTI),
        ("", "       … 1116 satir harita", GRIMTI, GRIMTI),
        ("$ ", "czip aralik a37tvc 40-52", YESIL, BEYAZ),
        ("", "{\"rol\": \"tool\", \"icerik\": …}", GRIMTI, BEYAZ),
        ("$ ", "czip ara a37tvc \"error\"", YESIL, BEYAZ),
        ("", "7 sonuc · jetonlar cozuldu", GRIMTI, MACENTA),
        ("$ ", "▌", YESIL, YESIL),
    ]
    sy = ty + 64
    for i, (p, m, pc, mc) in enumerate(satirlar):
        if p:
            d.text((tx + 22, sy), p, font=f_or, fill=pc)
            d.text((tx + 44, sy), m, font=f_or, fill=mc)
        else:
            d.text((tx + 44, sy), m, font=f_or, fill=mc)
        sy += 33

    # sag taraf: buyuk baslik
    f_bas = font(64, True)
    f_alt = font(26)
    f_oz = font(21)
    bx = 750
    metin_golge(d, (bx, 120), "czip", f_bas, YESIL)
    d.text((bx + 152, 150), ".hkp", font=f_alt, fill=GRIMTI)

    f_h = font(34, True)
    metin_golge(d, (bx, 210), "carry the session,", f_h, BEYAZ, 8)
    metin_golge(d, (bx, 256), "not the context.", f_h, CAMASIR, 8)

    oklar = [
        ("18x", "sıkıştırma, gerçek oturumda", YESIL),
        ("0", "bağımlılık — saf stdlib", MACENTA),
        ("100%", "kayıpsız round-trip testli", SARI),
    ]
    oy = 344
    for sayi, acik, renk in oklar:
        d.rectangle([bx, oy, bx + 4, oy + 26], fill=renk)
        d.text((bx + 18, oy - 3), sayi, font=f_alt, fill=renk)
        d.text((bx + 90, oy + 2), acik, font=f_oz, fill=GRIMTI)
        oy += 52

    # soltu alt bilgi
    d.line([(bx, 626), (W - 56, 626)], fill=(40, 60, 55))
    d.text((bx, 644), "github.com/Jilazem/hermes-czip", font=f_oz, fill=YESIL)
    d.text((bx + 420, 644), "CLI · 7-tool MCP · slash commands", font=f_oz,
           fill=GRIMTI)

    # buyutec ikonu (paket -> goz vurgusu): sag alt kosede
    d.ellipse([W - 170, 84, W - 106, 148], outline=CAMASIR, width=3)
    d.line([(W - 112, 142), (W - 88, 166)], fill=CAMASIR, width=5)
    d.text((W - 160, 100), "okur", font=f_kucuk, fill=CAMASIR)

    im.save(cikti, "PNG", optimize=True)
    print("kayit:", cikti)


if __name__ == "__main__":
    ciz(sys.argv[1] if len(sys.argv) > 1 else "assets/kapak.png")
