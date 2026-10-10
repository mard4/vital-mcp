"""Draw the README demo (docs/demo.gif): a scripted MCP session building a preset.

The tool calls and numbers below come from a real session with the server.
Pass the WAV from the final render_preset call to draw its waveform.

    uv run --no-project --with pillow python tools/make_demo_gif.py [render.wav]

Needs ffmpeg on PATH.
"""

import array
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 960, 600
FPS = 15
PAD_X, TOP, BOTTOM = 28, 58, 22
LINE = 22
OUT = Path(__file__).resolve().parent.parent / "docs" / "demo.gif"

FONT = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 14, index=0)
BOLD = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 14, index=1)
CHAR_W = FONT.getlength("M")

BG, CHROME = "#16161d", "#22222c"
FG, DIM, FAINT = "#e4e4e7", "#8b8b96", "#4a4a55"
ORANGE, GREEN, CYAN, PINK, YELLOW = "#d97757", "#7fd88f", "#7dcfff", "#f38ba8", "#f9e2af"

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Script:
    """Rows on a timeline. Each row appears at `at`, typed rows reveal over time."""

    def __init__(self):
        self.rows, self.t = [], 0.6

    def wait(self, sec):
        self.t += sec

    def line(self, *segs, type_cps=0, gap=0.12):
        text_len = sum(len(s[0]) for s in segs)
        self.rows.append({"kind": "text", "segs": segs, "at": self.t, "cps": type_cps})
        self.t += (text_len / type_cps if type_cps else 0) + gap

    def blank(self):
        self.rows.append({"kind": "blank", "at": self.t})

    def spinner(self, label, sec):
        self.rows.append({"kind": "spinner", "label": label, "at": self.t, "until": self.t + sec})
        self.t += sec

    def bars(self, bands, gap=0.08):
        for name, pct in bands:
            self.rows.append({"kind": "bar", "name": name, "pct": pct, "at": self.t})
            self.t += gap

    def wave(self, env):
        self.rows.append({"kind": "wave", "env": env, "at": self.t, "height": 3})
        self.t += 0.9


def row_height(row):
    return LINE * row.get("height", 1)


def tool_call(s, name, args):
    s.line(("● ", PINK), ("vital", PINK, True), (" · ", DIM), (name, CYAN, True), (f"({args})", DIM))


def build(env):
    s = Script()
    prompt = "Make a synth lead like the one in Blinding Lights by The Weeknd."
    s.line(("> ", ORANGE, True), (prompt, FG, True), type_cps=34, gap=0.2)
    s.line(("  ", FG), ("Then listen to it and fix anything that's off.", FG, True), type_cps=34, gap=0.7)
    s.blank()
    s.line(("✻ ", ORANGE), ("80s synthwave lead: detuned saws, chorus, dotted delay, big reverb.", DIM), gap=0.6)
    s.blank()

    tool_call(s, "create_preset", 'name="Blinding Lead", style="Lead"')
    s.spinner("writing preset", 0.9)
    for left, right in [
        ("osc 1   ", "saw · 5 voices · detune 12% · spread 80%"),
        ("osc 2   ", "pulse 35% · +12 st · 3 voices"),
        ("filter  ", "Analog 12dB · 2.2 kHz · env_2 → cutoff +0.25"),
        ("fx      ", "chorus 45% · ping-pong delay 1/8 dotted · reverb 3 s"),
        ("macros  ", "BRIGHT → cutoff · SPACE → reverb"),
    ]:
        s.line(("  └ ", FAINT), (left, DIM), (right, FG), gap=0.18)
    s.line(("  ✓ ", GREEN, True), ("Saved ", FG), ("User/Presets/MCP/Blinding Lead.vital", GREEN), gap=0.8)
    s.blank()

    tool_call(s, "render_preset", 'notes="riff", bpm=171')
    s.spinner("playing through Vital offline", 1.2)
    s.line(("  └ ", FAINT), ("peak ", DIM), ("-1.5 dBFS", YELLOW, True), ("   centroid ", DIM),
           ("999 Hz", YELLOW, True), ("   stereo corr ", DIM), ("0.25", FG), gap=0.15)
    s.bars([("low-mid", 78.4), ("mid", 18.0), ("high", 3.3)])
    s.wait(0.9)
    s.blank()
    s.line(("✻ ", ORANGE), ("Too dark for that bright lead, and the peak is hot. Opening the filter.", FG),
           type_cps=60, gap=0.7)
    s.blank()

    tool_call(s, "edit_preset", 'preset="Blinding Lead"')
    s.spinner("saving", 0.6)
    s.line(("  └ ", FAINT), ("filter_1_cutoff  ", DIM), ("2200 Hz → 4500 Hz", FG), gap=0.15)
    s.line(("  └ ", FAINT), ("volume           ", DIM), ("-6 dB → -9 dB", FG), gap=0.7)
    s.blank()

    tool_call(s, "render_preset", 'notes="riff", bpm=171')
    s.spinner("playing through Vital offline", 1.2)
    s.line(("  └ ", FAINT), ("peak ", DIM), ("-7.5 dBFS", GREEN, True), ("   centroid ", DIM),
           ("1213 Hz", GREEN, True), ("   stereo corr ", DIM), ("0.17", FG), gap=0.15)
    s.bars([("low-mid", 74.1), ("mid", 19.4), ("high", 4.3), ("air", 1.2)])
    if env:
        s.wave(env)
    s.wait(0.6)
    s.blank()
    s.line(("✓ ", GREEN, True), ("Done. In Vital: ", FG), ("User › Presets › MCP › Blinding Lead", ORANGE, True),
           gap=4.0)
    return s


def draw_segs(d, x, y, segs, limit=None):
    shown = 0
    for seg in segs:
        text, color = seg[0], seg[1]
        if limit is not None:
            text = text[: max(0, limit - shown)]
        shown += len(seg[0])
        d.text((x, y), text, font=BOLD if len(seg) > 2 else FONT, fill=color)
        x += CHAR_W * len(text)
        if limit is not None and shown >= limit:
            return x, True
    return x, False


def draw_row(d, row, y, t, frame):
    x = PAD_X
    kind = row["kind"]
    if kind == "text":
        limit = int((t - row["at"]) * row["cps"]) if row["cps"] else None
        end, typing = draw_segs(d, x, y, row["segs"], limit)
        if typing and frame % 8 < 5:
            d.rectangle([end + 1, y + 2, end + CHAR_W, y + 17], fill=FG)
    elif kind == "spinner":
        ch = SPINNER[frame % len(SPINNER)]
        draw_segs(d, x, y, [("  " + ch + " ", ORANGE), (row["label"] + "…", DIM)])
    elif kind == "bar":
        draw_segs(d, x, y, [("  └ ", FAINT), (f"{row['name']:<9}", DIM)])
        bx = x + CHAR_W * 13
        grow = min(1.0, (t - row["at"]) / 0.4)
        width = 360 * row["pct"] / 100 * grow
        d.rounded_rectangle([bx, y + 5, bx + 360, y + 15], radius=4, fill="#24242e")
        if width > 2:
            d.rounded_rectangle([bx, y + 5, bx + width, y + 15], radius=4, fill=CYAN)
        d.text((bx + 372, y), f"{row['pct']:.1f}%", font=FONT, fill=FG)
    elif kind == "wave":
        draw_segs(d, x, y, [("  └ ", FAINT)])
        bx, mid, half = x + CHAR_W * 4, y + LINE * 1.5, LINE * 1.3
        env = row["env"]
        reveal = min(1.0, (t - row["at"]) / 0.8)
        step = 520 / len(env)
        for i, a in enumerate(env[: int(len(env) * reveal)]):
            h = max(1.0, a * half)
            d.rectangle([bx + i * step, mid - h, bx + i * step + step * 0.6, mid + h], fill=ORANGE)


def visible(row, t):
    if row["at"] > t:
        return False
    return row["kind"] != "spinner" or t < row["until"]


def frame_image(script, t, frame, scroll):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    rows = [r for r in script.rows if visible(r, t)]
    total = sum(row_height(r) for r in rows)
    view = H - TOP - BOTTOM
    target = max(0, total - view)
    scroll += (target - scroll) * 0.35
    y = TOP - scroll
    for row in rows:
        h = row_height(row)
        if y + h > TOP - LINE:
            draw_row(d, row, y, t, frame)
        y += h
    d.rectangle([0, 0, W, TOP - 12], fill=CHROME)
    d.rectangle([0, TOP - 12, W, TOP - 4], fill=BG)
    for i, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        d.ellipse([18 + i * 22, 13, 30 + i * 22, 25], fill=color)
    title = "claude  ·  vital-mcp"
    d.text(((W - FONT.getlength(title)) / 2, 11), title, font=FONT, fill=DIM)
    d.rectangle([0, H - BOTTOM + 6, W, H], fill=BG)
    return img, scroll


def envelope(path, points=130):
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2, "expects the 16-bit WAVs render_preset writes"
        data = array.array("h", w.readframes(w.getnframes()))
        channels = w.getnchannels()
    mono = data[::channels]
    size = len(mono) // points
    peaks = [max(abs(v) for v in mono[i * size:(i + 1) * size]) for i in range(points)]
    top = max(peaks) or 1
    return [p / top for p in peaks]


def encode(images, out, fps=FPS, colors=64):
    """Write PIL images to a looping GIF with one shared palette."""
    tmp = Path(tempfile.mkdtemp())
    count = 0
    for count, img in enumerate(images, 1):
        img.save(tmp / f"{count:05d}.png")
    out.parent.mkdir(exist_ok=True)
    pattern = str(tmp / "%05d.png")
    palette = str(tmp / "palette.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-start_number", "1",
                    "-i", pattern, "-vf", f"palettegen=max_colors={colors}:stats_mode=full", palette],
                   check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-start_number", "1",
                    "-i", pattern, "-i", palette, "-lavfi", "paletteuse=dither=none:diff_mode=rectangle",
                    "-loop", "0", str(out)], check=True)
    shutil.rmtree(tmp)
    print(f"{out} ({count} frames, {out.stat().st_size / 1e6:.1f} MB)")


def main():
    env = envelope(sys.argv[1]) if len(sys.argv) > 1 else None
    script = build(env)

    def images():
        scroll = 0.0
        for i in range(int(script.t * FPS)):
            img, scroll = frame_image(script, i / FPS, i, scroll)
            yield img

    encode(images(), OUT)


if __name__ == "__main__":
    main()
