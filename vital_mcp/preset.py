"""Read, create, edit and describe Vital .vital presets (plain JSON)."""

import base64
import copy
import json
import os
import platform
import re
import shutil
import time
from pathlib import Path

import numpy as np

from . import params as P
from . import wavetable as W

NUM_MODULATIONS = 64
NUM_LFOS = 8
NUM_OSCILLATORS = 3
STYLES = ["Bass", "Lead", "Keys", "Pad", "Percussion", "Sequence", "Experiment", "SFX", "Template"]
EFFECTS = ["chorus", "compressor", "delay", "distortion", "eq", "filter_fx", "flanger", "phaser", "reverb"]

MOD_SOURCES = (
    [f"env_{i}" for i in range(1, 7)]
    + [f"lfo_{i}" for i in range(1, 9)]
    + [f"random_{i}" for i in range(1, 5)]
    + [f"macro_control_{i}" for i in range(1, 5)]
    + ["velocity", "note", "note_in_octave", "aftertouch", "slide", "lift",
       "stereo", "random", "mod_wheel", "pitch_wheel"]
)
SOURCE_ALIASES = {f"macro_{i}": f"macro_control_{i}" for i in range(1, 5)} | {
    f"macro{i}": f"macro_control_{i}" for i in range(1, 5)} | {"modwheel": "mod_wheel", "pitchbend": "pitch_wheel"}

# LFO shapes in natural coordinates (y = 1 is the top). Vital stores y inverted.
LFO_SHAPES = {
    "triangle": ([(0, 0), (0.5, 1), (1, 0)], False),
    "sine": ([(0, 0), (0.5, 1), (1, 0)], True),
    "saw_up": ([(0, 0), (1, 1), (1, 0)], False),
    "saw_down": ([(0, 1), (1, 0)], False),
    "square": ([(0, 1), (0.5, 1), (0.5, 0), (1, 0)], False),
    "ramp_up": ([(0, 0), (1, 1)], False),
    "ramp_down": ([(0, 1), (1, 0)], False),
    "constant": ([(0, 1), (1, 1)], False),
}


# --------------------------------------------------------------------------- paths

def config_path():
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library/Application Support/vital/Vital.config"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home())) / "vital/Vital.config"
    return Path.home() / ".local/share/vital/Vital.config"


def vital_config():
    try:
        return json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}


def data_dir():
    if os.environ.get("VITAL_DATA_DIR"):
        return Path(os.environ["VITAL_DATA_DIR"]).expanduser()
    configured = vital_config().get("data_directory")
    if configured:
        return Path(configured)
    if platform.system() == "Windows":
        return Path.home() / "Documents/Vital"
    return Path.home() / "Music/Vital"


def synth_version():
    return vital_config().get("synth_version", "1.6.4")


def output_dir(subfolder="MCP"):
    return data_dir() / "User" / "Presets" / subfolder


def find_presets(query="", limit=50):
    """Presets under the Vital data folder whose name, folder, style or author match."""
    words = query.lower().split()
    results = []
    for path in sorted(data_dir().rglob("*.vital")):
        if ".vital-mcp-backups" in path.parts:
            continue
        haystack = str(path.relative_to(data_dir())).lower()
        if words and not all(w in haystack for w in words):
            meta = _quick_meta(path)
            haystack += " " + " ".join(str(v).lower() for v in meta.values())
            if not all(w in haystack for w in words):
                continue
        results.append(path)
        if len(results) >= limit:
            break
    return results


def _quick_meta(path):
    try:
        data = json.loads(path.read_text())
        return {k: data.get(k, "") for k in ("preset_style", "author", "comments")}
    except (OSError, ValueError):
        return {}


def resolve(name_or_path):
    """Accept a full path, a path relative to the Vital folder, or a preset name."""
    candidate = Path(name_or_path).expanduser()
    for path in (candidate, data_dir() / candidate):
        if path.is_file():
            return path
        if path.with_suffix(".vital").is_file():
            return path.with_suffix(".vital")
    target = Path(name_or_path).stem.lower()
    matches = [p for p in data_dir().rglob("*.vital") if p.stem.lower() == target]
    if not matches:
        matches = [p for p in data_dir().rglob("*.vital") if target in p.stem.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"No preset matching '{name_or_path}' in {data_dir()}")
    listing = "\n".join(f"  {p.relative_to(data_dir())}" for p in matches[:10])
    raise FileNotFoundError(f"'{name_or_path}' matches several presets, pass a path:\n{listing}")


# --------------------------------------------------------------------------- load/save

def load(name_or_path):
    path = resolve(name_or_path)
    return json.loads(path.read_text()), path


def save(preset, path, overwrite=False):
    path = Path(path).expanduser()
    if path.suffix != ".vital":
        path = path.with_suffix(".vital")
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"{path} already exists. Pass overwrite=true or choose another name.")
        backups = data_dir() / "User" / ".vital-mcp-backups"
        backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backups / f"{path.stem}.{time.strftime('%Y%m%d-%H%M%S')}.vital")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Vital's browser reads the author from the first bytes, so keep "author" first
    # and the JSON compact like Vital writes it.
    ordered = {"author": preset.get("author", "")} | {k: v for k, v in preset.items() if k != "author"}
    path.write_text(json.dumps(ordered, separators=(",", ":")))
    return path


def init_preset(name="Init"):
    """A preset equivalent to Vital's init patch (saw osc, everything else default)."""
    settings = {n: d["default"] for n, d in P.schema().items()}
    settings["modulations"] = [{"source": "", "destination": ""} for _ in range(NUM_MODULATIONS)]
    settings["lfos"] = [lfo_shape("triangle") for _ in range(NUM_LFOS)]
    settings["wavetables"] = [W.build("Init", ["saw"], "spectral", author="", version=synth_version())
                              for _ in range(NUM_OSCILLATORS)]
    noise = np.random.default_rng(0).uniform(-0.25, 0.25, 44100)
    settings["sample"] = {
        "length": 44100, "name": "White Noise", "sample_rate": 44100,
        "samples": base64.b64encode((noise * 32767).astype("<i2").tobytes()).decode(),
    }
    return {
        "author": "", "comments": "",
        "macro1": "MACRO 1", "macro2": "MACRO 2", "macro3": "MACRO 3", "macro4": "MACRO 4",
        "preset_name": name, "preset_style": "", "settings": settings,
        "synth_version": synth_version(),
    }


# --------------------------------------------------------------------------- editing

def set_metadata(preset, name=None, author=None, style=None, comments=None, macro_names=None):
    if name is not None:
        preset["preset_name"] = name
    if author is not None:
        preset["author"] = author
    if comments is not None:
        preset["comments"] = comments
    if style is not None:
        matches = [s for s in STYLES if s.lower() == style.lower()]
        if style and not matches:
            raise ValueError(f"style must be one of {STYLES}")
        preset["preset_style"] = matches[0] if matches else ""
    for index, label in (macro_names or {}).items():
        digits = re.findall(r"\d", str(index))
        index = int(digits[-1]) if digits else 0
        if not 1 <= index <= 4:
            raise ValueError("Macro numbers are 1-4")
        preset[f"macro{index}"] = label


def apply_parameters(preset, values):
    """Set parameters from user-friendly values. Returns (changes, warnings)."""
    settings = preset["settings"]
    changes, warnings = [], []
    # Filter models first so style names resolve against the new model.
    ordered = sorted(values.items(), key=lambda kv: 0 if kv[0].endswith("_model") else 1)
    for name, value in ordered:
        raw, warning = P.from_user(name, value, settings)
        before = settings.get(name, P.details(name)["default"])
        settings[name] = raw
        changes.append(f"{name}: {P.to_display(name, before, settings)} -> {P.to_display(name, raw, settings)}")
        if warning:
            warnings.append(warning)
    return changes, warnings


def lfo_shape(spec):
    """LFO JSON from a shape name or {"points": [[x, y], ...], "powers": [...], "smooth": bool}."""
    if isinstance(spec, str):
        if spec not in LFO_SHAPES:
            raise ValueError(f"Unknown LFO shape '{spec}'. Use {list(LFO_SHAPES)} or custom points.")
        points, smooth = LFO_SHAPES[spec]
        name, powers = spec.replace("_", " ").title(), None
    else:
        points = spec["points"]
        smooth = bool(spec.get("smooth", False))
        name, powers = spec.get("name", "Custom"), spec.get("powers")
    if len(points) < 2 or points[0][0] != 0 or points[-1][0] != 1:
        raise ValueError("LFO points need at least 2 points, starting at x=0 and ending at x=1")
    flat = []
    for x, y in points:
        flat += [float(x), 1.0 - float(y)]
    powers = [float(p) for p in (powers or [0.0] * len(points))]
    return {"name": name, "num_points": len(points), "points": flat, "powers": powers, "smooth": smooth}


def set_lfo(preset, number, spec):
    if not 1 <= number <= NUM_LFOS:
        raise ValueError("LFO numbers are 1-8")
    preset["settings"]["lfos"][number - 1] = lfo_shape(spec)


def set_wavetable(preset, osc, spec):
    """spec: {"frames": [...], "interpolation": "time", "name": "..."} or
    {"morph": [start, end], "steps": 8} or a single frame spec like "square"."""
    if not 1 <= osc <= NUM_OSCILLATORS:
        raise ValueError("Oscillator numbers are 1-3")
    if isinstance(spec, (str,)) or (isinstance(spec, dict) and not ({"frames", "morph"} & spec.keys())):
        spec = {"frames": [spec]}
    name = spec.get("name") or W.table_name(spec["morph"] if "morph" in spec else spec["frames"])
    kwargs = {"author": preset.get("author", ""), "version": synth_version()}
    if "morph" in spec:
        table = W.morph(spec["morph"][0], spec["morph"][1], spec.get("steps", 8), name,
                        spec.get("interpolation", "time"), **kwargs)
    else:
        table = W.build(name, spec["frames"], spec.get("interpolation", "time"), spec.get("positions"), **kwargs)
    preset["settings"]["wavetables"][osc - 1] = table
    return W.summarize(table)


def _source(name):
    name = SOURCE_ALIASES.get(name.lower().replace(" ", "_"), name.lower().replace(" ", "_"))
    if name not in MOD_SOURCES:
        raise ValueError(f"Unknown modulation source '{name}'. Valid: {', '.join(MOD_SOURCES)}")
    return name


def _destination(name):
    d = P.details(name)
    if d["scale"] == "indexed":
        raise ValueError(f"{name} is a switch/menu and can't be modulated")
    return name


def _reset_slot(settings, index):
    settings["modulations"][index] = {"source": "", "destination": ""}
    for key in [k for k in P.schema() if k.startswith(f"modulation_{index + 1}_")]:
        settings[key] = P.details(key)["default"]


def set_modulations(preset, connections, clear_existing=False):
    """Add, update or remove modulation routings.

    Each connection: {"source": "lfo_1", "destination": "filter_1_cutoff",
    "amount": 0.4, "bipolar": false, "stereo": false, "bypass": false,
    "power": 0.0, "remove": false}. amount is a fraction of the destination's
    full range (-1..1). An existing source->destination pair is updated in place.
    """
    settings = preset["settings"]
    mods = settings["modulations"]
    while len(mods) < NUM_MODULATIONS:
        mods.append({"source": "", "destination": ""})
    if clear_existing:
        for index in range(NUM_MODULATIONS):
            _reset_slot(settings, index)

    log = []
    for conn in connections:
        source = _source(conn["source"])
        destination = _destination(conn["destination"])
        slot = next((i for i, m in enumerate(mods)
                     if m["source"] == source and m["destination"] == destination), None)
        if conn.get("remove"):
            if slot is None:
                log.append(f"no routing {source} -> {destination} to remove")
            else:
                _reset_slot(settings, slot)
                log.append(f"removed {source} -> {destination}")
            continue
        if slot is None:
            slot = next((i for i, m in enumerate(mods) if not m["source"] and not m["destination"]), None)
            if slot is None:
                raise ValueError("All 64 modulation slots are used")
            _reset_slot(settings, slot)
            mods[slot] = {"source": source, "destination": destination}
        prefix = f"modulation_{slot + 1}_"
        if "amount" in conn:
            amount = float(conn["amount"])
            if not -1.0 <= amount <= 1.0:
                raise ValueError("Modulation amount must be between -1 and 1")
            settings[prefix + "amount"] = amount
        for flag in ("bipolar", "stereo", "bypass"):
            if flag in conn:
                settings[prefix + flag] = 1.0 if conn[flag] else 0.0
        if "power" in conn:
            settings[prefix + "power"] = float(conn["power"])
        log.append(f"slot {slot + 1}: {source} -> {destination} amount {settings[prefix + 'amount']:+.2f}"
                   + (" bipolar" if settings.get(prefix + "bipolar") else ""))
    return log


# --------------------------------------------------------------------------- describing

def _get(settings, name):
    return settings.get(name, P.details(name)["default"])


def _fmt(settings, name):
    return P.to_display(name, _get(settings, name), settings)


def active_modulations(settings):
    result = []
    for index, m in enumerate(settings.get("modulations", [])):
        if m.get("source") and m.get("destination"):
            prefix = f"modulation_{index + 1}_"
            result.append({
                "slot": index + 1, "source": m["source"], "destination": m["destination"],
                "amount": round(settings.get(prefix + "amount", 0.0), 3),
                "bipolar": bool(settings.get(prefix + "bipolar", 0)),
                "bypass": bool(settings.get(prefix + "bypass", 0)),
            })
    return result


def _filter_blend(settings, f):
    model = int(_get(settings, f"filter_{f}_model"))
    blend = _get(settings, f"filter_{f}_blend")
    if model in (0, 1, 2, 3):  # analog, dirty, ladder, digital
        style = int(_get(settings, f"filter_{f}_style"))
        if style in (0, 1):
            label = {0.0: "low-pass", 1.0: "band-pass", 2.0: "high-pass"}.get(round(blend, 2))
            return label or f"blend {blend:.2f} (0=LP, 1=BP, 2=HP)"
    return f"blend {blend:.2f}"


def describe(preset):
    s = preset["settings"]
    lines = [f"# {preset.get('preset_name', 'Untitled')}"]
    meta = [f"style: {preset.get('preset_style') or '-'}", f"author: {preset.get('author') or '-'}",
            f"Vital {preset.get('synth_version', '?')}"]
    lines.append(" | ".join(meta))
    if preset.get("comments"):
        lines.append(f"comments: {preset['comments']}")

    mods = active_modulations(s)
    used_sources = {m["source"] for m in mods}

    lines.append("\n## Oscillators")
    for o in range(1, NUM_OSCILLATORS + 1):
        if not _get(s, f"osc_{o}_on"):
            continue
        table = s.get("wavetables", [{}] * 3)[o - 1]
        bits = [f"wavetable {W.summarize(table)}", f"level {_fmt(s, f'osc_{o}_level')}",
                f"-> {_fmt(s, f'osc_{o}_destination')}"]
        transpose, tune = _get(s, f"osc_{o}_transpose"), _get(s, f"osc_{o}_tune")
        if transpose or tune:
            bits.append(f"transpose {transpose:+.0f} st, tune {tune * 100:+.0f} ct")
        voices = int(_get(s, f"osc_{o}_unison_voices"))
        if voices > 1:
            bits.append(f"unison {voices} voices, detune {_fmt(s, f'osc_{o}_unison_detune')}, "
                        f"blend {_fmt(s, f'osc_{o}_unison_blend')}, stack {_fmt(s, f'osc_{o}_stack_style')}")
        bits.append(f"frame {_get(s, f'osc_{o}_wave_frame'):.0f}/256")
        if _get(s, f"osc_{o}_distortion_type"):
            bits.append(f"warp {_fmt(s, f'osc_{o}_distortion_type')} {_get(s, f'osc_{o}_distortion_amount'):.2f}")
        if _get(s, f"osc_{o}_spectral_morph_type"):
            bits.append(f"morph {_fmt(s, f'osc_{o}_spectral_morph_type')} "
                        f"{_get(s, f'osc_{o}_spectral_morph_amount'):.2f}")
        if _get(s, f"osc_{o}_pan"):
            bits.append(f"pan {_fmt(s, f'osc_{o}_pan')}")
        lines.append(f"- OSC {o}: " + "; ".join(bits))
    if _get(s, "sample_on"):
        lines.append(f"- SAMPLE: {s.get('sample', {}).get('name', '?')}; level {_fmt(s, 'sample_level')}; "
                     f"-> {_fmt(s, 'sample_destination')}")

    lines.append("\n## Filters")
    for f in ("1", "2"):
        if not _get(s, f"filter_{f}_on"):
            continue
        lines.append(
            f"- FILTER {f}: {_fmt(s, f'filter_{f}_model')} {_fmt(s, f'filter_{f}_style')} "
            f"({_filter_blend(s, f)}); cutoff {_fmt(s, f'filter_{f}_cutoff')}; "
            f"resonance {_get(s, f'filter_{f}_resonance'):.2f}; drive {_fmt(s, f'filter_{f}_drive')}; "
            f"keytrack {_get(s, f'filter_{f}_keytrack'):.2f}; mix {_fmt(s, f'filter_{f}_mix')}")
    if not any(_get(s, f"filter_{f}_on") for f in ("1", "2")):
        lines.append("- none active")

    lines.append("\n## Envelopes")
    for e in range(1, 7):
        if e != 1 and f"env_{e}" not in used_sources:
            continue
        label = " (amp)" if e == 1 else ""
        lines.append(f"- ENV {e}{label}: A {_fmt(s, f'env_{e}_attack')}, D {_fmt(s, f'env_{e}_decay')}, "
                     f"S {_get(s, f'env_{e}_sustain'):.2f}, R {_fmt(s, f'env_{e}_release')}")

    lfo_lines = []
    for n in range(1, NUM_LFOS + 1):
        if f"lfo_{n}" not in used_sources:
            continue
        shape = s.get("lfos", [{}] * 8)[n - 1].get("name", "?")
        sync = _fmt(s, f"lfo_{n}_sync")
        rate = _fmt(s, f"lfo_{n}_tempo") if "Tempo" in sync else _fmt(s, f"lfo_{n}_frequency")
        lfo_lines.append(f"- LFO {n}: {shape}, {rate} ({sync}), mode {_fmt(s, f'lfo_{n}_sync_type')}")
    for n in range(1, 5):
        if f"random_{n}" in used_sources:
            lfo_lines.append(f"- RANDOM {n}: {_fmt(s, f'random_{n}_style')}")
    if lfo_lines:
        lines.append("\n## LFOs")
        lines += lfo_lines

    if mods:
        lines.append("\n## Modulation")
        for m in mods:
            flags = (" bipolar" if m["bipolar"] else "") + (" (bypassed)" if m["bypass"] else "")
            lines.append(f"- {m['source']} -> {m['destination']}: {m['amount']:+.2f}{flags}")

    macros = []
    for i in range(1, 5):
        targets = [m["destination"] for m in mods if m["source"] == f"macro_control_{i}"]
        if targets or _get(s, f"macro_control_{i}"):
            macros.append(f"- {preset.get(f'macro{i}', f'MACRO {i}')} = {_get(s, f'macro_control_{i}'):.2f}"
                          + (f" -> {', '.join(targets)}" if targets else ""))
    if macros:
        lines.append("\n## Macros")
        lines += macros

    lines.append("\n## Effects")
    on = [fx for fx in EFFECTS if _get(s, f"{fx}_on")]
    for fx in on:
        names = [n for n in P.schema() if n.startswith(fx + "_") and n != f"{fx}_on"]
        if fx == "filter_fx":
            names = [n for n in names if n.split("_")[-1] in ("model", "style", "cutoff", "resonance", "mix", "drive")]
        values = ", ".join(f"{n[len(fx) + 1:]} {_fmt(s, n)}" for n in sorted(names))
        lines.append(f"- {fx.upper()}: {values}")
    if not on:
        lines.append("- none active")

    lines.append("\n## Voice")
    lines.append(f"- volume {_fmt(s, 'volume')}; polyphony {_fmt(s, 'polyphony')}; legato {_fmt(s, 'legato')}; "
                 f"glide {_fmt(s, 'portamento_time')}; velocity track {_get(s, 'velocity_track'):.2f}")
    return "\n".join(lines)


def get_parameters(preset, names=None, prefix=None, non_default_only=False):
    s = preset["settings"]
    selected = names or [n for n in P.schema() if not prefix or n.startswith(prefix)]
    result = {}
    for name in selected:
        raw = _get(s, name)
        if non_default_only and abs(raw - P.details(name)["default"]) < 1e-6:
            continue
        result[name] = P.to_display(name, raw, s)
    return result


def diff(a, b):
    sa, sb = a["settings"], b["settings"]
    changes = []
    for name in P.schema():
        va, vb = _get(sa, name), _get(sb, name)
        if abs(va - vb) > 1e-6 and not name.startswith("modulation_"):
            changes.append(f"{name}: {P.to_display(name, va, sa)} -> {P.to_display(name, vb, sb)}")
    ma = {(m["source"], m["destination"]): m["amount"] for m in active_modulations(sa)}
    mb = {(m["source"], m["destination"]): m["amount"] for m in active_modulations(sb)}
    for key in sorted(ma.keys() | mb.keys()):
        if ma.get(key) != mb.get(key):
            changes.append(f"mod {key[0]} -> {key[1]}: {ma.get(key, 'none')} -> {mb.get(key, 'none')}")
    for i in range(NUM_OSCILLATORS):
        wa = W.summarize(sa.get("wavetables", [{}] * 3)[i])
        wb = W.summarize(sb.get("wavetables", [{}] * 3)[i])
        if wa != wb:
            changes.append(f"osc {i + 1} wavetable: {wa} -> {wb}")
    return changes


def safe_filename(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return cleaned or "Untitled"


def clone(preset):
    return copy.deepcopy(preset)
