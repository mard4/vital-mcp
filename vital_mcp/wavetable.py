"""Build Vital wavetables from simple descriptions.

A Vital wavetable is a list of keyframes placed at positions 0..256, each holding
one 2048-sample cycle stored as base64 little-endian float32.
"""

import base64

import numpy as np

FRAME_SIZE = 2048
MAX_POSITION = 256
SHAPES = ("sine", "saw", "ramp", "square", "triangle", "pulse", "noise")

_PHASE = np.arange(FRAME_SIZE) / FRAME_SIZE
_INTERPOLATION = {"none": (0, 0), "time": (0, 1), "spectral": (1, 1)}


def shape(name, width=0.5, seed=0):
    """One cycle of a basic waveform."""
    p = _PHASE
    if name == "sine":
        wave = np.sin(2 * np.pi * p)
    elif name == "saw":
        wave = 1.0 - 2.0 * p
    elif name == "ramp":
        wave = 2.0 * p - 1.0
    elif name == "square":
        wave = np.where(p < 0.5, 1.0, -1.0)
    elif name == "pulse":
        wave = np.where(p < width, 1.0, -1.0)
    elif name == "triangle":
        wave = 1.0 - 4.0 * np.abs(((p + 0.25) % 1.0) - 0.5)
    elif name == "noise":
        wave = np.random.default_rng(seed).uniform(-1.0, 1.0, FRAME_SIZE)
    else:
        raise ValueError(f"Unknown shape '{name}'. Use one of {SHAPES}")
    return wave


def harmonics(amplitudes, phases=None):
    """Additive cycle: amplitudes[0] is the fundamental, [1] the 2nd harmonic, ..."""
    wave = np.zeros(FRAME_SIZE)
    for index, amplitude in enumerate(amplitudes[: FRAME_SIZE // 2]):
        if amplitude:
            phase = phases[index] if phases and index < len(phases) else 0.0
            wave += amplitude * np.sin(2 * np.pi * (index + 1) * _PHASE + phase)
    return wave


def frame_from_spec(spec):
    """Turn one keyframe description into a cycle.

    spec forms:
      "saw"                                        basic shape
      {"shape": "pulse", "width": 0.2}
      {"harmonics": [1, 0.5, 0.33]}                additive partials
      {"samples": [...]}                           raw cycle, resampled to 2048
      {"mix": [{"shape": "saw"}, {"shape": "sine"}], "weights": [0.3, 0.7]}
      any of the above plus "fold": 2.0 (wavefold drive) or "drive": 3.0 (tanh)
    """
    if isinstance(spec, str):
        spec = {"shape": spec}
    if "shape" in spec:
        wave = shape(spec["shape"], spec.get("width", 0.5), spec.get("seed", 0))
    elif "harmonics" in spec:
        wave = harmonics(spec["harmonics"], spec.get("phases"))
    elif "samples" in spec:
        samples = np.asarray(spec["samples"], dtype=float)
        wave = np.interp(_PHASE * len(samples), np.arange(len(samples)), samples, period=len(samples))
    elif "mix" in spec:
        parts = [frame_from_spec(s) for s in spec["mix"]]
        weights = spec.get("weights") or [1.0 / len(parts)] * len(parts)
        wave = sum(w * p for w, p in zip(weights, parts))
    else:
        raise ValueError(f"Can't build a frame from {spec}")

    if spec.get("drive"):
        wave = np.tanh(wave * spec["drive"])
    if spec.get("fold"):
        wave = np.sin(np.pi / 2 * wave * spec["fold"])
    return normalize(wave)


def normalize(wave):
    wave = wave - wave.mean()
    peak = np.max(np.abs(wave))
    return wave / peak if peak > 0 else wave


def encode(wave):
    return base64.b64encode(np.asarray(wave, dtype="<f4").tobytes()).decode()


def decode(data):
    return np.frombuffer(base64.b64decode(data), dtype="<f4")


def build(name, frames, interpolation="time", positions=None, author="vital-mcp", version="1.6.4"):
    """Wavetable JSON for Vital from a list of frame specs.

    Frames are spread evenly across the table (positions 0..256) unless explicit
    positions are given. interpolation: "time" (crossfade), "spectral"
    (harmonic morph, Vital's default) or "none" (stepped).
    """
    if not frames:
        raise ValueError("A wavetable needs at least one frame")
    if interpolation not in _INTERPOLATION:
        raise ValueError(f"interpolation must be one of {list(_INTERPOLATION)}")
    if positions is None:
        count = len(frames)
        positions = [0] if count == 1 else [round(i * MAX_POSITION / (count - 1)) for i in range(count)]
    mode, style = _INTERPOLATION[interpolation]
    keyframes = [{"position": int(pos), "wave_data": encode(frame_from_spec(spec))}
                 for pos, spec in zip(positions, frames)]
    return {
        "author": author,
        "full_normalize": True,
        "remove_all_dc": True,
        "name": name,
        "version": version,
        "groups": [{"components": [{
            "type": "Wave Source",
            "interpolation": mode,
            "interpolation_style": style,
            "keyframes": keyframes,
        }]}],
    }


def morph(start, end, steps=8, name=None, interpolation="time", **kwargs):
    """Convenience: a table that sweeps between two frame specs."""
    a, b = frame_from_spec(start), frame_from_spec(end)
    frames = [{"samples": list((1 - t) * a + t * b)} for t in np.linspace(0, 1, steps)]
    return build(name or "Morph", frames, interpolation, **kwargs)


def summarize(wavetable):
    """Short text description of an existing wavetable."""
    parts = []
    for group in wavetable.get("groups", []):
        for component in group.get("components", []):
            frames = len(component.get("keyframes", []))
            parts.append(f"{component.get('type')} ({frames} keyframe{'s' if frames != 1 else ''})"
                         if frames else component.get("type"))
    return f"{wavetable.get('name', 'unnamed')}: " + ", ".join(parts)
