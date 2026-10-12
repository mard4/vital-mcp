"""Parameter schema plus conversion between Vital's stored values and display units.

Vital stores every control as a raw float. The UI shows it as
display = display_multiply * skew(raw) + post_offset, where skew depends on the
parameter's scale (quadratic, quartic, exponential, ...). Cutoff-style
parameters are stored as MIDI note numbers, which we present in Hz.
"""

import difflib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.json")

# Present in the open-source parameter table but no longer written by Vital 1.5+.
LEGACY = re.compile(r"^(sub_.*|filter_(1|2|fx)_(osc\d|sample)_input|filter_fx_filter_input|compressor_low_band_unused)$")

# The filter style list depends on the filter model.
FILTER_STYLES = {
    "Diode": ["Low Shelf", "Low Cut"],
    "Comb": ["Low High Comb", "Low High Flange+", "Low High Flange-",
             "Band Spread Comb", "Band Spread Flange+", "Band Spread Flange-"],
}

ON_WORDS = {"on": 1.0, "true": 1.0, "yes": 1.0, "off": 0.0, "false": 0.0, "no": 0.0}


class ParameterError(ValueError):
    pass


@lru_cache(maxsize=1)
def schema():
    data = json.loads(SCHEMA_PATH.read_text())
    return {name: details for name, details in data.items() if not LEGACY.match(name)}


def details(name):
    try:
        return schema()[name]
    except KeyError:
        key = name.replace(" ", "_").lower()
        close = [n for n in schema() if key in n][:8] or difflib.get_close_matches(key, schema(), n=5)
        hint = f" Similar: {', '.join(close)}" if close else ""
        raise ParameterError(f"Unknown parameter '{name}'.{hint}") from None


def is_frequency(d):
    # Stored as MIDI note numbers (8..136 or 0..128); chorus/delay cutoffs lack the units label.
    return d["units"] in ("semitones", "") and d["max"] >= 128 and re.search(r"cutoff|center", d["name"]) is not None


def midi_to_hz(note):
    return 440.0 * 2.0 ** ((note - 69.0) / 12.0)


def hz_to_midi(hz):
    if hz <= 0:
        raise ParameterError("Frequency must be positive")
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def _skew(d, raw):
    scale = d["scale"]
    if scale == "quadratic":
        return raw * raw
    if scale == "cubic":
        return raw ** 3
    if scale == "quartic":
        return raw ** 4
    if scale == "squareroot":
        return math.sqrt(max(raw, 0.0))
    if scale == "exponential":
        return 1.0 / 2.0 ** raw if d["display_invert"] else 2.0 ** raw
    return raw


def _unskew(d, value):
    scale = d["scale"]
    if scale == "quadratic":
        return math.sqrt(max(value, 0.0))
    if scale == "cubic":
        return math.copysign(abs(value) ** (1 / 3), value)
    if scale == "quartic":
        return max(value, 0.0) ** 0.25
    if scale == "squareroot":
        return value * value
    if scale == "exponential":
        if value <= 0:
            raise ParameterError(f"{d['name']} needs a positive value")
        return math.log2(1.0 / value) if d["display_invert"] else math.log2(value)
    return value


def options_for(name, settings=None):
    d = details(name)
    match = re.match(r"filter_(1|2|fx)_style$", name)
    if match and settings is not None:
        model = settings.get(f"filter_{match.group(1)}_model")
        models = details(f"filter_{match.group(1)}_model")["options"]
        if model is not None and 0 <= int(model) < len(models):
            return FILTER_STYLES.get(models[int(model)], d.get("options"))
    return d.get("options")


def to_display(name, raw, settings=None):
    """Raw stored value -> human readable string."""
    d = details(name)
    options = options_for(name, settings)
    if options:
        index = int(round(raw))
        return options[index] if 0 <= index < len(options) else str(index)
    if is_frequency(d):
        return f"{midi_to_hz(raw):.0f} Hz"
    value = d["display_multiply"] * _skew(d, raw) + d["post_offset"]
    units = d["units"]
    if units == "secs":
        return f"{value * 1000:.3g} ms" if value < 1 else f"{value:.3g} s"
    if d["scale"] == "indexed":
        return f"{value:.0f}{(' ' + units) if units else ''}"
    text = f"{value:.3g}" if abs(value) < 100 else f"{value:.0f}"
    if units == "%":
        return text + "%"
    return f"{text} {units}".strip()


_TEMPO_FEEL = re.compile(r"^\s*(\d+/\d+)\s*(d|dot|dotted|\.|t|trip|triplets?)\s*$", re.I)
_NUMBER_UNIT = re.compile(r"^\s*(-?[0-9]*\.?[0-9]+(?:e-?\d+)?)\s*(k?hz|khz|k|ms|s|secs?|seconds?|%|db|st|semitones?|cents?)?\s*$", re.I)


def from_user(name, value, settings=None):
    """User value -> (raw value, warning or None).

    Accepts a number in display units, a string with units ("800 Hz", "250 ms",
    "-6 dB", "40%"), an option name ("Ladder", "1/8"), on/off for switches,
    or {"raw": x} / "raw:x" to bypass conversion.
    """
    d = details(name)
    raw = None

    if isinstance(value, dict) and "raw" in value:
        raw = float(value["raw"])
    elif isinstance(value, bool):
        raw = 1.0 if value else 0.0
    elif isinstance(value, str) and value.strip().lower().startswith("raw:"):
        raw = float(value.split(":", 1)[1])
    elif isinstance(value, str) and value.strip().lower() in ON_WORDS and d["max"] == 1.0 and d["scale"] == "indexed":
        raw = ON_WORDS[value.strip().lower()]
    elif isinstance(value, str) and options_for(name, settings):
        options = options_for(name, settings)
        lowered = [o.lower() for o in options]
        wanted = value.strip().lower()
        if wanted in lowered:  # checked before numbers so "24dB" or "1/8" pick the menu entry
            raw = float(lowered.index(wanted))
        elif _NUMBER_UNIT.match(value):
            raw = _parse_numeric(d, value)
        else:
            feel = _TEMPO_FEEL.match(value) if name.endswith("_tempo") else None
            if feel:
                sync = name[:-len("tempo")] + "sync"
                kind = "Tempo Triplets" if feel.group(2).lower().startswith("t") else "Tempo Dotted"
                raise ParameterError(f"{name}: dotted/triplet values are set on {sync}; "
                                     f"use {sync}='{kind}' and {name}='{feel.group(1)}'")
            raise ParameterError(f"{name}: '{value}' is not one of {options}")
    else:
        raw = _parse_numeric(d, value)

    warning = None
    lo, hi = d["min"], d["max"]
    if raw < lo or raw > hi:
        clamped = min(max(raw, lo), hi)
        warning = (f"{name}: {value!r} is out of range "
                   f"({to_display(name, lo, settings)} to {to_display(name, hi, settings)}); "
                   f"clamped to {to_display(name, clamped, settings)}")
        raw = clamped
    if d["scale"] == "indexed":
        raw = float(round(raw))
    return raw, warning


def _parse_numeric(d, value):
    unit = ""
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = _NUMBER_UNIT.match(str(value))
        if not match:
            raise ParameterError(f"{d['name']}: can't understand value {value!r}")
        number = float(match.group(1))
        unit = (match.group(2) or "").lower()

    if is_frequency(d):
        if unit in ("khz", "k"):
            return hz_to_midi(number * 1000)
        if unit in ("st", "semitone", "semitones"):
            return number
        return hz_to_midi(number)  # plain numbers are Hz for cutoffs

    if unit == "ms":
        number /= 1000.0
    if d["display_multiply"] == 100.0 and unit != "%" and d["units"] == "%" and abs(number) <= 1.0 and not isinstance(value, str):
        # A bare 0..1 for a percentage parameter is treated as a fraction.
        number *= 100.0

    return _unskew(d, (number - d["post_offset"]) / d["display_multiply"])


def describe_parameter(name):
    d = details(name)
    info = {
        "name": name,
        "label": d["display_name"],
        "range": f"{to_display(name, d['min'])} .. {to_display(name, d['max'])}",
        "default": to_display(name, d["default"]),
    }
    if d.get("options"):
        info["options"] = d["options"]
    if is_frequency(d):
        info["input"] = "Hz (e.g. 800, '2.5 kHz')"
    elif d["units"]:
        info["units"] = d["units"]
    return info
