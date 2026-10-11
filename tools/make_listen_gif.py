"""Draw docs/listen.gif: what render_preset measures, and the listen-fix-listen loop.

The numbers are the two real render_preset results from the Blinding Lead session.

    uv run --no-project --with pillow python tools/make_listen_gif.py [first.wav second.wav]

Needs ffmpeg on PATH.
"""

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from make_demo_gif import encode, envelope
from make_desktop_gif import (BG, BORDER, CARD, FAINT, GOOD, GREEN, INK, MUTED, ORANGE, SANS,
                              SANS_BOLD, SANS_MED, SANS_SM, WARN, check, pill, sf)

W, H = 960, 540
FPS = 15
OUT = Path(__file__).resolve().parent.parent / "docs" / "listen.gif"
TITLE = sf(17, "Semibold")

BEFORE = {"peak": -1.5, "rms": -21.2, "centroid": 999, "corr": 0.25,
          "bands": [0.0, 0.1, 78.4, 18.0, 3.3, 0.3]}
AFTER = {"peak": -7.5, "rms": -25.4, "centroid": 1213, "corr": 0.17,
         "bands": [0.0, 0.9, 74.1, 19.4, 4.3, 1.2]}
BANDS = ["sub", "low", "low-mid", "mid", "high", "air"]

STEPS = ["Play it through Vital", "Measure the sound", "Fix the preset", "Play it again"]
# (start, end) of each phase in seconds
PLAY1, MEASURE, FIX, PLAY2, REMEASURE, END = (0.6, 3.4), (3.4, 4.6), (6.4, 8.6), (8.6, 11.2), (11.2, 12.6), 17.0


def ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def progress(t, span):
    return ease((t - span[0]) / (span[1] - span[0]))


def lerp(a, b, k):
    return a + (b - a) * k


def tag(d, box, text, colors):
    """Pill aligned right, on the value's row."""
    pill(d, box[2] - 16 - SANS_SM.getlength(text) - 16, box[1] + 36, text, colors)


def card(d, box, label):
    d.rounded_rectangle(box, radius=12, fill=CARD, outline=BORDER)
    d.text((box[0] + 16, box[1] + 12), label, font=SANS_SM, fill=MUTED)


def draw_wave(d, box, env_a, env_b, t):
    card(d, box, "Audio")
    x0, y0, x1, y1 = box[0] + 16, box[1] + 34, box[2] - 16, box[3] - 14
    mid, half = (y0 + y1) / 2, (y1 - y0) / 2
    if t < PLAY2[0]:
        env, k = env_a, progress(t, PLAY1)
    else:
        env, k = env_b, progress(t, PLAY2)
    step = (x1 - x0) / len(env)
    for i, a in enumerate(env):
        x = x0 + i * step
        played = i / len(env) < k
        h = max(1.0, a * half)
        d.rounded_rectangle([x, mid - h, x + step * 0.55, mid + h], radius=1,
                            fill=ORANGE if played else "#ece8de")
    if 0 < k < 1:
        px = x0 + (x1 - x0) * k
        d.line([px, y0 - 4, px, y1 + 4], fill=INK, width=2)


def metric_state(t):
    """Blend from nothing to BEFORE, then BEFORE to AFTER."""
    shown = progress(t, MEASURE)
    k = progress(t, REMEASURE)
    vals = {key: lerp(BEFORE[key], AFTER[key], k) for key in ("peak", "rms", "centroid", "corr")}
    vals["bands"] = [lerp(a, b, k) for a, b in zip(BEFORE["bands"], AFTER["bands"])]
    return shown, k, vals


def draw_loudness(d, box, t):
    shown, k, v = metric_state(t)
    card(d, box, "Loudness")
    x0, x1, y = box[0] + 16, box[2] - 16, box[1] + 76
    d.rounded_rectangle([x0, y, x1, y + 12], radius=6, fill="#efebe2")
    pos = lambda db: x0 + (x1 - x0) * (db + 48) / 48
    if shown > 0:
        d.rounded_rectangle([x0, y, lerp(x0, pos(v["rms"]), shown), y + 12], radius=6, fill="#e9b9a6")
        px = lerp(x0, pos(v["peak"]), shown)
        d.rectangle([px - 1.5, y - 5, px + 1.5, y + 17], fill=ORANGE)
        d.text((x0, box[1] + 38), f"peak {v['peak']:.1f} dB", font=SANS_MED, fill=INK)
    d.rectangle([pos(0) - 1, y - 2, pos(0) + 1, y + 14], fill="#c84b3c")
    d.text((pos(0) - 6, y + 18), "0", font=SANS_SM, fill=FAINT)
    d.text((x0, y + 18), "-48 dB", font=SANS_SM, fill=FAINT)
    if shown >= 1:
        tag(d, box, *(("healthy headroom", GOOD) if k > 0.5 else ("close to clipping", WARN)))


def draw_brightness(d, box, t):
    shown, k, v = metric_state(t)
    card(d, box, "Brightness")
    x0, x1, y = box[0] + 16, box[2] - 16, box[1] + 76
    lo, hi = math.log10(100), math.log10(10000)
    pos = lambda hz: x0 + (x1 - x0) * (math.log10(hz) - lo) / (hi - lo)
    for i in range(10):
        c = tuple(int(lerp(a, b, i / 9)) for a, b in ((0x3a, 0xf3), (0x55, 0xc9), (0x8f, 0x6b)))
        d.rectangle([x0 + (x1 - x0) * i / 10, y, x0 + (x1 - x0) * (i + 1) / 10, y + 12], fill=c)
    if shown > 0:
        cx = lerp(x0, pos(v["centroid"]), shown)
        d.ellipse([cx - 8, y - 2, cx + 8, y + 14], fill=CARD, outline=INK, width=2)
        d.text((x0, box[1] + 38), f"{v['centroid']:.0f} Hz", font=SANS_MED, fill=INK)
    d.text((x0, y + 18), "dark", font=SANS_SM, fill=FAINT)
    d.text((x1 - SANS_SM.getlength("bright"), y + 18), "bright", font=SANS_SM, fill=FAINT)
    if shown >= 1:
        tag(d, box, *(("brighter", GOOD) if k > 0.5 else ("a bit dull", WARN)))


def draw_stereo(d, box, t):
    shown, k, v = metric_state(t)
    card(d, box, "Stereo width")
    x0, x1, y = box[0] + 16, box[2] - 16, box[1] + 76
    d.rounded_rectangle([x0, y, x1, y + 12], radius=6, fill="#efebe2")
    width = 1 - v["corr"]
    if shown > 0:
        cx, half = (x0 + x1) / 2, (x1 - x0) / 2 * width * shown
        d.rounded_rectangle([cx - half, y, cx + half, y + 12], radius=6, fill="#9fc3e8")
        d.text((x0, box[1] + 38), "wide" if width > 0.6 else "medium", font=SANS_MED, fill=INK)
    d.text((x0, y + 18), "mono", font=SANS_SM, fill=FAINT)
    d.text((x1 - SANS_SM.getlength("wide"), y + 18), "wide", font=SANS_SM, fill=FAINT)
    if shown >= 1:
        tag(d, box, "spacious", GOOD)


def draw_bands(d, box, t):
    shown, k, v = metric_state(t)
    card(d, box, "Energy per band")
    n = len(BANDS)
    x0, x1 = box[0] + 24, box[2] - 24
    base, top = box[3] - 30, box[1] + 50
    slot = (x1 - x0) / n
    for i, (name, pct) in enumerate(zip(BANDS, v["bands"])):
        bx = x0 + i * slot + slot * 0.2
        bw = slot * 0.6
        d.rounded_rectangle([bx, top, bx + bw, base], radius=5, fill="#f3f0e8")
        h = (base - top) * pct / 100 * shown
        if h > 1:
            d.rounded_rectangle([bx, base - h, bx + bw, base], radius=5, fill=ORANGE)
        if shown > 0:
            label = f"{pct:.0f}%" if pct >= 1 or pct == 0 else f"{pct:.1f}%"
            d.text((bx + bw / 2 - SANS_SM.getlength(label) / 2, base - h - 18), label, font=SANS_SM, fill=MUTED)
        d.text((bx + bw / 2 - SANS_SM.getlength(name) / 2, base + 6), name, font=SANS_SM, fill=MUTED)


def draw_steps(d, t):
    spans = [PLAY1, MEASURE, FIX, (PLAY2[0], END)]
    if t >= REMEASURE[0]:
        active = 3
    else:
        active = max([i for i, s in enumerate(spans) if t >= s[0]] or [0])
    widths = [SANS.getlength(s) + 48 for s in STEPS]
    gap = 26
    x = (W - sum(widths) - gap * 3) / 2
    y = H - 50
    for i, (label, w) in enumerate(zip(STEPS, widths)):
        on, done = i == active, i < active
        d.rounded_rectangle([x, y, x + w, y + 32], radius=16,
                            fill=ORANGE if on else CARD, outline=ORANGE if on else BORDER)
        num_fill = "#ffffff" if on else (GREEN if done else FAINT)
        if done:
            check(d, x + 12, y + 11, GREEN)
        else:
            d.text((x + 14, y + 7), str(i + 1), font=SANS_BOLD, fill=num_fill)
        d.text((x + 34, y + 6), label, font=SANS, fill="#ffffff" if on else INK)
        if i < 3:
            ax = x + w + gap / 2
            d.line([ax - 6, y + 16, ax + 6, y + 16], fill=FAINT, width=2)
            d.line([ax + 2, y + 12, ax + 6, y + 16, ax + 2, y + 20], fill=FAINT, width=2)
        x += w + gap


def draw_fix(d, t):
    k = progress(t, (FIX[0], FIX[0] + 0.5))
    if t < FIX[0] or t >= PLAY2[0] + 0.6:
        return
    w, h = 470, 92
    x, y = (W - w) / 2, 348 + (1 - k) * 12
    d.rounded_rectangle([x, y, x + w, y + h], radius=12, fill=CARD, outline=ORANGE, width=2)
    d.rounded_rectangle([x + 14, y + 12, x + 36, y + 34], radius=6, fill="#f4e4dc")
    d.text((x + 20.5, y + 14), "V", font=SANS_BOLD, fill=ORANGE)
    d.text((x + 46, y + 14), "Edit preset", font=SANS_MED, fill=INK)
    d.text((x + 46, y + 42), "Cutoff  2.2 kHz  →  4.5 kHz", font=SANS, fill=INK)
    d.text((x + 46, y + 64), "Volume  −6 dB  →  −9 dB", font=SANS, fill=INK)


def frame(t, envs):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((24, 18), "render_preset", font=TITLE, fill=INK)
    d.text((24 + TITLE.getlength("render_preset") + 10, 21), "Blinding Lead · riff at 171 BPM, offline in Vital",
           font=SANS, fill=MUTED)
    draw_wave(d, [24, 54, W - 24, 152], *envs, t)
    third = (W - 48 - 32) / 3
    for i, fn in enumerate((draw_loudness, draw_brightness, draw_stereo)):
        x = 24 + i * (third + 16)
        fn(d, [x, 166, x + third, 300], t)
    draw_bands(d, [24, 314, W - 24, 472], t)
    draw_fix(d, t)
    draw_steps(d, t)
    return img


def main():
    if len(sys.argv) > 2:
        envs = [envelope(path, points=120) for path in sys.argv[1:3]]
    else:
        envs = [[0.5] * 120, [0.4] * 120]
    encode((frame(i / FPS, envs) for i in range(int(END * FPS))), OUT, fps=FPS, colors=128)


if __name__ == "__main__":
    main()
