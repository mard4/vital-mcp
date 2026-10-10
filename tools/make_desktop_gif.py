"""Draw docs/demo-desktop.gif: the same session as make_demo_gif.py, as a chat in a desktop app.

    uv run --no-project --with pillow python tools/make_desktop_gif.py [first.wav second.wav]

The WAVs are the two render_preset results, drawn as the audio players.

Needs ffmpeg on PATH.
"""

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from make_demo_gif import encode, envelope

W, H = 960, 600
FPS = 15
COL_X, COL_W = 170, 620
CHAT_TOP, CHAT_BOTTOM = 52, H - 104
OUT = Path(__file__).resolve().parent.parent / "docs" / "demo-desktop.gif"


def sf(size, weight="Regular"):
    font = ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", size)
    font.set_variation_by_name(weight)
    return font


SANS, SANS_SM, SANS_MED, SANS_BOLD = sf(15), sf(13), sf(14, "Medium"), sf(14, "Semibold")
SERIF = ImageFont.truetype("/System/Library/Fonts/NewYork.ttf", 16)
SERIF.set_variation_by_axes([16, 400, 0])  # text optical size, not the display cut

BG, CHROME, BORDER, CARD = "#faf9f5", "#f0eee6", "#e3dfd4", "#ffffff"
INK, MUTED, FAINT = "#29261b", "#7b776c", "#b7b2a5"
ORANGE, GREEN = "#d97757", "#2f8a4c"
WARN = ("#fbecd2", "#8a5a12")
GOOD = ("#dcf1e1", "#1f6b37")


def wrap(text, font, width):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if font.getlength(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur] if cur else lines


def spark(d, cx, cy, r, color, angle=0.0):
    for i in range(4):
        a = angle + i * math.pi / 4
        dx, dy = math.cos(a) * r, math.sin(a) * r
        d.line([cx - dx, cy - dy, cx + dx, cy + dy], fill=color, width=3)


def check(d, x, y, color, s=1.0):
    d.line([x, y + 5 * s, x + 4 * s, y + 9 * s, x + 11 * s, y], fill=color, width=2, joint="curve")


def spinner(d, cx, cy, r, frame, color):
    start = (frame * 40) % 360
    d.arc([cx - r, cy - r, cx + r, cy + r], start, start + 270, fill=color, width=2)


def pill(d, x, y, text, colors):
    bg, fg = colors
    w = SANS_SM.getlength(text) + 16
    d.rounded_rectangle([x, y, x + w, y + 20], radius=10, fill=bg)
    d.text((x + 8, y + 3), text, font=SANS_SM, fill=fg)


class UserBubble:
    def __init__(self, at, text):
        self.at, self.lines = at, wrap(text, SANS, 430)

    def height(self, t):
        return len(self.lines) * 22 + 24 + 22

    def draw(self, d, y, t, frame):
        w = max(SANS.getlength(line) for line in self.lines) + 32
        x = COL_X + COL_W - w
        d.rounded_rectangle([x, y, x + w, y + len(self.lines) * 22 + 22], radius=16, fill=CHROME)
        for i, line in enumerate(self.lines):
            d.text((x + 16, y + 11 + i * 22), line, font=SANS, fill=INK)


class Thinking:
    def __init__(self, at, until, label):
        self.at, self.until, self.label = at, until, label

    def height(self, t):
        return 34 if t < self.until else 0

    def draw(self, d, y, t, frame):
        spark(d, COL_X + 8, y + 9, 7, ORANGE, frame * 0.25)
        d.text((COL_X + 26, y), self.label, font=SANS, fill=MUTED)


class AssistantText:
    def __init__(self, at, text, cps=75):
        self.at, self.cps = at, cps
        self.lines = wrap(text, SERIF, COL_W)
        self.duration = len(text) / cps

    def shown(self, t):
        budget = int((t - self.at) * self.cps)
        out = []
        for line in self.lines:
            if budget <= 0:
                break
            out.append(line[:budget])
            budget -= len(line) + 1
        return out

    def height(self, t):
        return len(self.shown(t)) * 25 + 16

    def draw(self, d, y, t, frame):
        for i, line in enumerate(self.shown(t)):
            d.text((COL_X, y + i * 25), line, font=SERIF, fill=INK)


class ToolCard:
    """Collapsed tool call that runs, then expands into readable rows."""

    ROW = 28

    def __init__(self, at, title, run, rows):
        self.at, self.title, self.done, self.rows = at, title, at + run, rows

    def visible_rows(self, t):
        if t < self.done:
            return []
        return [r for i, r in enumerate(self.rows) if t >= self.done + 0.15 + i * 0.14]

    def row_h(self, row):
        return 64 if row[0] == "audio" else self.ROW

    def height(self, t):
        rows = self.visible_rows(t)
        body = sum(self.row_h(r) for r in rows) + (14 if rows else 0)
        return 44 + body + 14

    def draw(self, d, y, t, frame):
        h = self.height(t) - 14
        d.rounded_rectangle([COL_X, y, COL_X + COL_W, y + h], radius=12, fill=CARD, outline=BORDER)
        d.rounded_rectangle([COL_X + 12, y + 11, COL_X + 34, y + 33], radius=6, fill="#f4e4dc")
        d.text((COL_X + 18.5, y + 13), "V", font=SANS_BOLD, fill=ORANGE)
        d.text((COL_X + 46, y + 13), self.title, font=SANS_MED, fill=INK)
        d.text((COL_X + 52 + SANS_MED.getlength(self.title), y + 14), "Vital", font=SANS_SM, fill=FAINT)
        if t < self.done:
            spinner(d, COL_X + COL_W - 24, y + 22, 7, frame, ORANGE)
        else:
            check(d, COL_X + COL_W - 30, y + 17, GREEN, 1.1)
        rows = self.visible_rows(t)
        if not rows:
            return
        ry = y + 44
        d.line([COL_X + 1, ry, COL_X + COL_W - 1, ry], fill=BORDER)
        ry += 10
        for row in rows:
            self.draw_row(d, row, ry, t)
            ry += self.row_h(row)

    def draw_row(self, d, row, y, t):
        x = COL_X + 46
        if row[0] == "kv":
            _, key, value, *tag = row
            d.text((x, y + 2), key, font=SANS_SM, fill=MUTED)
            d.text((x + 110, y + 1), value, font=SANS, fill=INK)
            if tag:
                pill(d, x + 120 + SANS.getlength(value), y, *tag[0])
        elif row[0] == "audio":
            env = row[1]
            d.rounded_rectangle([x - 4, y, COL_X + COL_W - 16, y + 52], radius=10, fill=BG)
            d.ellipse([x + 6, y + 10, x + 38, y + 42], fill=ORANGE)
            d.polygon([(x + 18, y + 18), (x + 18, y + 34), (x + 30, y + 26)], fill="#ffffff")
            bx, mid, span = x + 54, y + 26, COL_X + COL_W - 76 - (x + 54)
            step = span / len(env)
            for i, a in enumerate(env):
                hh = max(1.0, a * 17)
                d.rounded_rectangle([bx + i * step, mid - hh, bx + i * step + step * 0.55, mid + hh],
                                    radius=1, fill=ORANGE if a > 0.04 else FAINT)
            d.text((COL_X + COL_W - 58, y + 18), "0:04", font=SANS_SM, fill=MUTED)


def build(envs):
    first, second = envs or ([0.0] * 90, [0.0] * 90)
    prompt = ("Make me a synth lead like the one in Blinding Lights by The Weeknd, "
              "then listen to it and fix anything that sounds off.")
    blocks, t = [], 0.8
    typing = (t, t + len(prompt) / 30)
    t = typing[1] + 0.5
    blocks.append(UserBubble(t, prompt))
    t += 0.4
    blocks.append(Thinking(t, t + 1.4, "Designing the sound…"))
    t += 1.4
    text = AssistantText(t, "Going for that 80s synthwave lead: bright detuned saws, lush chorus, "
                            "a dotted delay and a big reverb.")
    blocks.append(text)
    t += text.duration + 0.3
    card = ToolCard(t, "Create preset", 1.1, [
        ("kv", "Oscillators", "5 detuned saws + a pulse an octave up"),
        ("kv", "Filter", "Analog low-pass at 2.2 kHz, plucky envelope"),
        ("kv", "Effects", "Chorus, dotted ping-pong delay, 3 s reverb"),
        ("kv", "Macros", "BRIGHT and SPACE"),
    ])
    blocks.append(card)
    t = card.done + 1.6
    card = ToolCard(t, "Listen to preset", 1.5, [
        ("audio", first),
        ("kv", "Brightness", "999 Hz", ("a bit dull", WARN)),
        ("kv", "Loudness", "peak −1.5 dB", ("close to clipping", WARN)),
    ])
    blocks.append(card)
    t = card.done + 1.6
    text = AssistantText(t, "It's a little dull and peaking close to 0 dB. "
                            "I'll open up the filter and turn it down a bit.")
    blocks.append(text)
    t += text.duration + 0.3
    card = ToolCard(t, "Edit preset", 0.8, [
        ("kv", "Cutoff", "2.2 kHz  →  4.5 kHz"),
        ("kv", "Volume", "−6 dB  →  −9 dB"),
    ])
    blocks.append(card)
    t = card.done + 1.2
    card = ToolCard(t, "Listen to preset", 1.5, [
        ("audio", second),
        ("kv", "Brightness", "1213 Hz", ("brighter", GOOD)),
        ("kv", "Loudness", "peak −7.5 dB", ("healthy headroom", GOOD)),
    ])
    blocks.append(card)
    t = card.done + 1.4
    text = AssistantText(t, "Done! Open Vital and find it under User › Presets › MCP › Blinding Lead. "
                            "Turn BRIGHT for more shimmer and SPACE for more reverb.")
    blocks.append(text)
    end = t + text.duration + 4.0
    return blocks, prompt, typing, end


def draw_input(d, prompt, typing, t, frame):
    top = H - 92
    d.rectangle([0, top - 12, W, H], fill=BG)
    box = [COL_X - 10, top, COL_X + COL_W + 10, H - 18]
    d.rounded_rectangle(box, radius=18, fill=CARD, outline=BORDER)
    start, end = typing
    if start <= t < end + 0.5:
        typed = prompt[: int((t - start) * 30)]
        lines = wrap(typed, SANS, COL_W - 70) or [""]
        lines = lines[-2:]
        for i, line in enumerate(lines):
            d.text((COL_X + 8, top + 14 + i * 21), line, font=SANS, fill=INK)
        if frame % 8 < 5:
            cx = COL_X + 9 + SANS.getlength(lines[-1])
            cy = top + 14 + (len(lines) - 1) * 21
            d.line([cx, cy + 1, cx, cy + 18], fill=INK, width=2)
        ready = True
    else:
        d.text((COL_X + 8, top + 14), "Reply to Claude…", font=SANS, fill=FAINT)
        ready = False
    d.text((COL_X + 8, H - 44), "+", font=sf(20), fill=MUTED)
    bx, by = COL_X + COL_W - 22, H - 37
    d.rounded_rectangle([bx - 14, by - 14, bx + 14, by + 14], radius=9, fill=ORANGE if ready else "#e8c2b2")
    d.line([bx, by + 7, bx, by - 6], fill="#ffffff", width=2)
    d.line([bx - 5, by - 1, bx, by - 6, bx + 5, by - 1], fill="#ffffff", width=2)


def fade(height=18):
    """BG-coloured strip, opaque at the top, that softens content scrolling under the title bar."""
    strip = Image.new("RGBA", (W, height), BG)
    strip.putalpha(Image.linear_gradient("L").resize((W, height)).point(lambda v: 255 - v))
    return strip


def draw_chrome(d):
    d.rectangle([0, 0, W, 40], fill=CHROME)
    d.line([0, 40, W, 40], fill=BORDER)
    for i, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        d.ellipse([16 + i * 20, 14, 28 + i * 20, 26], fill=color)
    title = "Blinding Lights synth"
    d.text(((W - SANS_MED.getlength(title)) / 2, 11), title, font=SANS_MED, fill=INK)
    spark(d, W - 30, 20, 7, ORANGE)


def main():
    envs = [envelope(path, points=90) for path in sys.argv[1:3]] if len(sys.argv) > 2 else None
    blocks, prompt, typing, end = build(envs)

    top_fade = fade()

    def images():
        scroll = 0.0
        for frame in range(int(end * FPS)):
            t = frame / FPS
            img = Image.new("RGB", (W, H), BG)
            d = ImageDraw.Draw(img)
            live = [b for b in blocks if b.at <= t]
            total = sum(b.height(t) for b in live)
            target = max(0, total - (CHAT_BOTTOM - CHAT_TOP - 16))
            scroll += (target - scroll) * 0.3
            y = CHAT_TOP + 16 - scroll
            for b in live:
                h = b.height(t)
                if h and y + h > 0:
                    b.draw(d, y, t, frame)
                y += h
            img.paste(top_fade, (0, 41), top_fade)
            draw_chrome(d)
            draw_input(d, prompt, typing, t, frame)
            yield img

    encode(images(), OUT, fps=FPS, colors=128)


if __name__ == "__main__":
    main()
