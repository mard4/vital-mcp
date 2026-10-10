"""Render presets to audio offline and describe how they sound.

Vital is hosted with DawDreamer (optional dependency: pip install "vital-mcp[render]").
DawDreamer can't load .vital files directly, but Vital's plugin state is the preset
JSON wrapped by JUCE's VST3 layer, so we take a state from the plugin, swap the JSON
for the preset's and load it back:

  "VC2!" <uint32 LE length> <XML, NUL>          JUCE binary XML
    <VST3PluginState><IComponent>size.chars       JUCE base64 (own alphabet, LSB first)
      ... "CcnK" fxChunk header ... <chunk>       VST2-style program chunk
        <preset JSON> NUL <JUCE private data>     what Vital's getStateInformation wrote
"""

import importlib.util
import json
import multiprocessing
import os
import platform
import re
import struct
import tempfile
import time
import wave
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import numpy as np

from .preset import env_setting

SAMPLE_RATE = 44100
BLOCK_SIZE = 512
MAX_SECONDS = 30.0
DEFAULT_TAIL = 2.0

# Rough frequency bands, in Hz, for the tonal-balance summary.
BANDS = {"sub": (20, 60), "low": (60, 250), "low_mid": (250, 1000), "mid": (1000, 4000),
         "high": (4000, 10000), "air": (10000, 20000)}

# Note patterns in beats: (note, start, length, velocity).
PATTERNS = {
    "note": [(60, 0, 2, 100)],
    "riff": [(n, i * 0.5, 0.45, 100) for i, n in enumerate([60, 62, 64, 67, 69, 67, 64, 62])],
    "chord": [(n, 0, 3, 90) for n in (48, 60, 64, 67, 71)],
    "bass": [(n, i * 0.5, 0.4, 110) for i, n in enumerate([36, 36, 48, 36, 39, 36, 43, 41])],
    "pluck": [(n, i * 0.25, 0.2, 100) for i, n in enumerate([72, 67, 64, 60, 72, 67, 64, 60])],
}

_NOTE_NAMES = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
_JUCE_CHARS = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"
_JUCE_LOOKUP = np.zeros(256, dtype=np.uint8)
_JUCE_LOOKUP[np.frombuffer(_JUCE_CHARS.encode(), dtype=np.uint8)] = np.arange(64)


MISSING_DAWDREAMER = (
    "Rendering isn't installed. Claude Desktop: install vital-mcp-render.mcpb from "
    "https://github.com/mard4/vital-mcp/releases/latest instead of vital-mcp.mcpb. Other clients: "
    "use 'vital-mcp[render] @ git+https://github.com/mard4/vital-mcp' as the uvx --from value "
    "(or pip install \"vital-mcp[render]\"), then restart the server.")


class RenderError(RuntimeError):
    pass


# --- Plugin and DawDreamer -------------------------------------------------------------

def _plugin_candidates():
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return [Path("/Library/Audio/Plug-Ins/VST3/Vital.vst3"),
                home / "Library/Audio/Plug-Ins/VST3/Vital.vst3"]
    if system == "Windows":
        common = Path(os.environ.get("COMMONPROGRAMFILES", r"C:\Program Files\Common Files"))
        return [common / "VST3/Vital.vst3"]
    return [home / ".vst3/Vital.vst3", Path("/usr/lib/vst3/Vital.vst3"),
            Path("/usr/local/lib/vst3/Vital.vst3")]


def plugin_path():
    """Vital's VST3, from VITAL_PLUGIN_PATH or the standard install folders."""
    override = env_setting("VITAL_PLUGIN_PATH")
    if override:
        path = Path(override).expanduser()
        if not path.exists():
            raise RenderError(f"VITAL_PLUGIN_PATH points to {path}, which doesn't exist")
        return path
    for path in _plugin_candidates():
        if path.exists():
            return path
    raise RenderError("Vital.vst3 not found. Install Vital or set VITAL_PLUGIN_PATH to the .vst3 file.")


_ENGINE = {}


def _engine():
    """(engine, synth) for Vital, created once: loading the plugin takes a moment."""
    path = plugin_path()
    if _ENGINE.get("path") != path:
        try:
            import dawdreamer
        except ImportError as exc:
            raise RenderError(MISSING_DAWDREAMER) from exc
        engine = dawdreamer.RenderEngine(SAMPLE_RATE, BLOCK_SIZE)
        synth = engine.make_plugin_processor("vital", str(path))
        _ENGINE.update(path=path, engine=engine, synth=synth)
    return _ENGINE["engine"], _ENGINE["synth"]


# --- JUCE state ------------------------------------------------------------------------

def juce_base64_decode(text):
    """Decode JUCE's MemoryBlock::toBase64Encoding ("size.chars", 6 bits per char, LSB first)."""
    size, _, chars = text.strip().partition(".")
    size = int(size)
    values = _JUCE_LOOKUP[np.frombuffer(chars.encode(), dtype=np.uint8)]
    bits = np.unpackbits(values[:, None], axis=1, bitorder="little")[:, :6].ravel()[: size * 8]
    return np.packbits(bits, bitorder="little").tobytes()[:size]


def juce_base64_encode(data):
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8), bitorder="little")
    bits = np.concatenate([bits, np.zeros(-len(bits) % 6, dtype=np.uint8)]).reshape(-1, 6)
    values = bits @ (1 << np.arange(6))
    alphabet = np.frombuffer(_JUCE_CHARS.encode(), dtype=np.uint8)
    return f"{len(data)}." + alphabet[values].tobytes().decode()


def _split_xml(state):
    if state[:4] != b"VC2!":
        raise RenderError("Unexpected plugin state format (no JUCE XML header)")
    length = struct.unpack("<I", state[4:8])[0]
    return state[8:8 + length].rstrip(b"\0").decode("utf-8")


def _join_xml(xml):
    data = xml.encode("utf-8") + b"\0"
    return b"VC2!" + struct.pack("<I", len(data)) + data


def _component(xml):
    match = re.search(r"<IComponent>(.*?)</IComponent>", xml, re.S)
    if not match:
        raise RenderError("Unexpected plugin state format (no IComponent)")
    return match


def _chunk_bounds(component):
    """(start, end) of the program chunk inside a decoded IComponent blob."""
    header = component.find(b"CcnK")
    if header < 0 or component[header + 8:header + 12] != b"FBCh":
        raise RenderError("Unexpected plugin state format (no fxChunk)")
    start = header + 160
    size = struct.unpack(">I", component[header + 156:start])[0]
    return header, start, start + size


def preset_from_state(state):
    """The preset JSON stored in a Vital plugin state."""
    component = juce_base64_decode(_component(_split_xml(state)).group(1))
    _, start, end = _chunk_bounds(component)
    chunk = component[start:end]
    return json.loads(chunk[: chunk.index(b"\0")] if b"\0" in chunk else chunk)


def state_with_preset(state, preset):
    """Copy of a Vital plugin state with its preset JSON replaced."""
    xml = _split_xml(state)
    match = _component(xml)
    component = bytearray(juce_base64_decode(match.group(1)))
    header, start, end = _chunk_bounds(component)
    chunk = component[start:end]
    json_end = chunk.index(b"\0") if b"\0" in chunk else len(chunk)
    new_json = json.dumps(preset, separators=(",", ":")).encode("utf-8")
    new_chunk = new_json + chunk[json_end:]
    component = component[:start] + new_chunk + component[end:]
    struct.pack_into(">I", component, header + 156, len(new_chunk))
    struct.pack_into(">I", component, header + 4, struct.unpack(">I", component[header + 4:header + 8])[0]
                     + len(new_chunk) - (end - start))
    xml = xml[:match.start(1)] + juce_base64_encode(bytes(component)) + xml[match.end(1):]
    return _join_xml(xml)


def load_preset(synth, preset):
    """Load preset JSON into a DawDreamer Vital processor and check it took."""
    with tempfile.TemporaryDirectory() as tmp:
        current = os.path.join(tmp, "current.bin")
        synth.save_state(current)
        with open(current, "rb") as f:
            state = f.read()
        target = os.path.join(tmp, "preset.bin")
        with open(target, "wb") as f:
            f.write(state_with_preset(state, preset))
        synth.load_state(target)
        synth.save_state(current)
        with open(current, "rb") as f:
            loaded = preset_from_state(f.read())
    if loaded.get("preset_name") != preset.get("preset_name"):
        raise RenderError("Vital didn't accept the preset state; this Vital version may store it differently")


# --- Notes -----------------------------------------------------------------------------

def note_number(note):
    """MIDI number from 60, "60" or a name like "C4", "F#2", "Bb3" (C4 = 60)."""
    if isinstance(note, (int, float)):
        return int(note)
    text = str(note).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    match = re.fullmatch(r"([a-gA-G])([#b]?)(-?\d+)", text)
    if not match:
        raise ValueError(f"Can't read note '{note}'. Use a MIDI number or a name like C4, F#2, Bb3")
    letter, accidental, octave = match.groups()
    offset = {"#": 1, "b": -1}.get(accidental, 0)
    return 12 * (int(octave) + 1) + _NOTE_NAMES[letter.lower()] + offset


def parse_notes(notes, transpose=0):
    """Pattern name or list of {"note", "start", "length", "velocity"} (start/length in beats)
    into [(note, start_beats, length_beats, velocity)]."""
    if isinstance(notes, str):
        if notes not in PATTERNS:
            raise ValueError(f"Unknown pattern '{notes}'. Use one of {list(PATTERNS)} or a list of notes")
        events = PATTERNS[notes]
    else:
        events = []
        for i, item in enumerate(notes or []):
            if not isinstance(item, dict) or "note" not in item:
                raise ValueError(f"Note {i} needs at least a 'note' key, e.g. {{'note': 'C4', 'start': 0}}")
            events.append((note_number(item["note"]), float(item.get("start", i * 0.5)),
                           float(item.get("length", 0.45)), int(item.get("velocity", 100))))
        if not events:
            raise ValueError("No notes to play")
    result = []
    for note, start, length, velocity in events:
        note += transpose
        if not 0 <= note <= 127:
            raise ValueError(f"Note {note} is outside the MIDI range 0-127")
        result.append((note, start, length, max(1, min(127, velocity))))
    return result


# --- Rendering -------------------------------------------------------------------------

def render_dir():
    path = Path(tempfile.gettempdir()) / "vital-mcp" / "renders"
    path.mkdir(parents=True, exist_ok=True)
    return path


def render(preset, notes="riff", bpm=120.0, tail=DEFAULT_TAIL, transpose=0):
    """Play notes through Vital with the preset loaded. Returns (audio[2, n], events, info)."""
    if not 20 <= bpm <= 400:
        raise ValueError("bpm must be between 20 and 400")
    events = parse_notes(notes, transpose)
    beat = 60.0 / bpm
    played = max((start + length) * beat for _, start, length, _ in events)
    seconds = played + max(0.0, tail)
    if seconds > MAX_SECONDS:
        raise ValueError(f"Render would be {seconds:.1f} s; keep it under {MAX_SECONDS:g} s")

    if importlib.util.find_spec("dawdreamer") is None:
        raise RenderError(MISSING_DAWDREAMER)
    plugin_path()
    audio = _run_in_worker(_play, preset, events, bpm, seconds)
    info = {"bpm": bpm, "notes": len(events), "notes_end_sec": round(played, 2),
            "first_note_sec": round(min(e[1] for e in events) * beat, 3),
            "first_note_len_sec": round(min(events, key=lambda e: e[1])[2] * beat, 3)}
    return audio, events, info


def _play(preset, events, bpm, seconds):
    """Runs in the render worker process."""
    engine, synth = _engine()
    load_preset(synth, preset)
    engine.set_bpm(bpm)
    synth.clear_midi()
    beat = 60.0 / bpm
    for note, start, length, velocity in events:
        synth.add_midi_note(note, velocity, start * beat, length * beat)
    engine.load_graph([(synth, [])])
    engine.render(seconds)
    return np.asarray(engine.get_audio(), dtype=np.float64)


# Plugins (JUCE) must be created and driven from a process's main thread, but MCP servers
# run tools in worker threads. So the plugin lives in a separate process, where tasks run
# on the main thread; it stays loaded between renders, and a plugin crash can't take the
# server down.
_WORKER = {}


def _worker_init():
    # The MCP protocol runs over our stdout; keep anything the plugin prints off it.
    os.dup2(2, 1)


def _run_in_worker(fn, *args):
    if "pool" not in _WORKER:
        _WORKER["pool"] = ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn"),
                                              initializer=_worker_init)
    try:
        return _WORKER["pool"].submit(fn, *args).result()
    except BrokenProcessPool as exc:
        _WORKER.pop("pool").shutdown(wait=False)
        raise RenderError("The Vital render process crashed; try again (it restarts on the next render)") from exc


def write_wav(audio, path):
    """16-bit stereo WAV; audio is [channels, samples] in -1..1."""
    data = (np.clip(audio.T, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(data.shape[1])
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(data.tobytes())
    return path


def output_path(name, out_path=None):
    if out_path:
        path = Path(out_path).expanduser()
        if path.suffix.lower() != ".wav":
            raise ValueError("out_path must end in .wav")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    safe = re.sub(r"[^\w\- ]+", "", name).strip() or "render"
    stem = f"{safe} {time.strftime('%Y%m%d-%H%M%S')}"
    path, count = render_dir() / f"{stem}.wav", 2
    while path.exists():
        path, count = render_dir() / f"{stem} {count}.wav", count + 1
    return path


def read_wav(path):
    """[channels, samples] float audio and sample rate from a PCM WAV (16/24/32-bit)."""
    with wave.open(str(path), "rb") as wav:
        width, channels, rate = wav.getsampwidth(), wav.getnchannels(), wav.getframerate()
        if wav.getnframes() > 120 * rate:
            raise ValueError("Reference is longer than 120 seconds; trim it first")
        raw = wav.readframes(wav.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2") / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ints = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        data = np.where(ints >= 1 << 23, ints - (1 << 24), ints) / float(1 << 23)
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4") / float(1 << 31)
    else:
        raise ValueError("Only 16, 24 or 32-bit PCM WAV files are supported")
    return data.reshape(-1, channels).T.astype(np.float64), rate


# --- Analysis --------------------------------------------------------------------------

def _db(x):
    # None rather than -inf for silence: strict JSON has no -Infinity.
    return round(20 * np.log10(x), 1) if x > 0 else None


def _envelope(mono, rate, window=0.005):
    hop = max(1, int(rate * window))
    frames = len(mono) // hop
    if not frames:
        return np.zeros(1), hop
    return np.sqrt(np.mean(mono[: frames * hop].reshape(frames, hop) ** 2, axis=1)), hop


def analyze(audio, rate=SAMPLE_RATE, info=None):
    """Numbers a model can reason about: levels, stereo image, tonal balance, envelope."""
    audio = np.atleast_2d(audio)
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    result = {
        "duration_sec": round(audio.shape[1] / rate, 2),
        "peak_dbfs": _db(peak),
        "rms_dbfs": _db(float(np.sqrt(np.mean(audio ** 2)))) if audio.size else None,
        "clipped_samples": int(np.sum(np.abs(audio) >= 0.999)),
        "silent": peak < 1e-4,
    }
    if result["silent"]:
        result["hint"] = ("Silent. Check that an oscillator is on and routed (osc_N_on, osc_N_destination), "
                          "the volume isn't at minimum, and the notes are in a playable range.")
        return result

    mono = audio.mean(axis=0)
    env, hop = _envelope(mono, rate)
    loud = env[env > env.max() * 0.1]
    result["crest_factor_db"] = round(_db(peak) - _db(float(np.sqrt(np.mean(audio ** 2)))), 1)
    result["dynamics_db"] = round(_db(float(np.percentile(loud, 95))) - _db(float(np.percentile(loud, 10))), 1) \
        if len(loud) > 4 and np.percentile(loud, 10) > 0 else None

    if audio.shape[0] > 1:
        left, right = audio[0], audio[-1]
        l_rms, r_rms = np.sqrt(np.mean(left ** 2)), np.sqrt(np.mean(right ** 2))
        result["balance_db"] = round(_db(r_rms) - _db(l_rms), 1) if l_rms > 0 and r_rms > 0 else None
        if left.std() > 0 and right.std() > 0:
            # 1 = mono, ~0 = wide, <0 = phase problems
            result["stereo_correlation"] = round(float(np.corrcoef(left, right)[0, 1]), 2)
        side = (left - right) / 2
        mid = (left + right) / 2
        result["side_to_mid_db"] = round(_db(float(np.sqrt(np.mean(side ** 2)))) - _db(float(np.sqrt(np.mean(mid ** 2)))), 1) \
            if np.any(side) and np.any(mid) else None

    # Sum per-channel spectra so out-of-phase material still shows up.
    power = (np.abs(np.fft.rfft(audio, axis=1)) ** 2).sum(axis=0)
    freqs = np.fft.rfftfreq(audio.shape[1], 1 / rate)
    energy = {name: float(power[(freqs >= lo) & (freqs < hi)].sum()) for name, (lo, hi) in BANDS.items()}
    total = sum(energy.values()) or 1.0
    result["band_energy_pct"] = {name: round(100 * e / total, 1) for name, e in energy.items()}
    result["spectral_centroid_hz"] = round(float((freqs * power).sum() / power.sum()))
    cumulative = np.cumsum(power) / power.sum()
    result["rolloff_85_hz"] = round(float(freqs[np.searchsorted(cumulative, 0.85)]))

    if info:
        result.update(_note_timing(env, hop, rate, info))
    return result


def _note_timing(env, hop, rate, info):
    """Attack of the first note and how long the sound rings after the last note-off."""
    timing = {}
    start = int(info["first_note_sec"] * rate / hop)
    stop = start + max(1, int((info["first_note_len_sec"] + 0.05) * rate / hop))
    first = env[start:stop]
    if len(first) and first.max() > 0:
        timing["attack_ms"] = round(float(np.argmax(first >= 0.9 * first.max())) * hop / rate * 1000)
        held = first[len(first) // 2:]
        timing["sustain_level_pct"] = round(100 * float(held.mean() / first.max())) if len(held) else None
    end = int(info["notes_end_sec"] * rate / hop)
    floor = env.max() * 10 ** (-60 / 20)
    after = env[end:]
    below = np.nonzero(after < floor)[0]
    tail = (below[0] if len(below) else len(after)) * hop / rate
    timing["tail_sec"] = round(float(tail), 2)
    if not len(below):
        timing["tail_note"] = "still ringing at the end of the render; raise tail to hear all of it"
    return timing


def compare(render_stats, reference_stats):
    """Where the render differs from the reference, in plain numbers."""
    diff = {}
    if render_stats.get("silent") or reference_stats.get("silent"):
        return diff
    diff["centroid_ratio"] = round(render_stats["spectral_centroid_hz"] / reference_stats["spectral_centroid_hz"], 2)
    diff["band_energy_delta_pct"] = {
        band: round(render_stats["band_energy_pct"][band] - reference_stats["band_energy_pct"][band], 1)
        for band in BANDS}
    for key in ("rms_dbfs", "crest_factor_db", "stereo_correlation", "side_to_mid_db"):
        a, b = render_stats.get(key), reference_stats.get(key)
        if a is not None and b is not None:
            diff[f"{key}_delta"] = round(a - b, 2)
    return diff
