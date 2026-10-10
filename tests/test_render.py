import importlib.util
import struct

import numpy as np
import pytest

from vital_mcp import preset as V
from vital_mcp import render as R


def fake_state(preset_json, private=b"JUCEPrivateData\0\0\0\x01"):
    """A Vital VST3 state laid out the way JUCE writes it."""
    chunk = preset_json + b"\0" + private
    fx = (b"CcnK" + struct.pack(">I", 152 + len(chunk)) + b"FBCh" + struct.pack(">III", 2, 0x56697461, 1)
          + struct.pack(">I", 0) + bytes(128) + struct.pack(">I", len(chunk)) + chunk)
    component = b"VstW" + bytes(12) + fx
    xml = (f'<?xml version="1.0" encoding="UTF-8"?> <VST3PluginState><IComponent>'
           f'{R.juce_base64_encode(component)}</IComponent></VST3PluginState>')
    return R._join_xml(xml)


@pytest.mark.parametrize("data", [b"", b"a", b"VstW\x00\xff", bytes(range(256)) * 3])
def test_juce_base64_roundtrip(data):
    assert R.juce_base64_decode(R.juce_base64_encode(data)) == data


def test_juce_base64_matches_juce():
    # "ab" = 0x61 0x62, read LSB first in 6-bit groups: 100001 100100 0110(00) -> 33, 9, 6
    assert R.juce_base64_encode(b"ab") == "2.gIF"


def test_state_preset_swap():
    state = fake_state(b'{"preset_name":"Init","settings":{}}')
    assert R.preset_from_state(state)["preset_name"] == "Init"
    preset = {"preset_name": "Blinding Synth", "settings": {"volume": 0.5}}
    swapped = R.state_with_preset(state, preset)
    assert R.preset_from_state(swapped) == preset
    component = R.juce_base64_decode(R._component(R._split_xml(swapped)).group(1))
    header, start, end = R._chunk_bounds(component)
    assert component[end - 19:end].startswith(b"JUCEPrivateData")
    assert struct.unpack(">I", component[header + 4:header + 8])[0] == len(component) - header - 8


def test_bad_state():
    with pytest.raises(R.RenderError):
        R.preset_from_state(b"nope")


@pytest.mark.parametrize("note,number", [(60, 60), ("60", 60), ("C4", 60), ("F#2", 42), ("Bb3", 58), ("c-1", 0)])
def test_note_number(note, number):
    assert R.note_number(note) == number


def test_parse_notes():
    assert len(R.parse_notes("riff")) == 8
    assert R.parse_notes([{"note": "E4", "start": 1, "length": 2}], transpose=-12) == [(52, 1.0, 2.0, 100)]
    with pytest.raises(ValueError, match="pattern"):
        R.parse_notes("polka")
    with pytest.raises(ValueError, match="MIDI range"):
        R.parse_notes("note", transpose=100)
    with pytest.raises(ValueError, match="note"):
        R.parse_notes([{"start": 0}])


def test_analyze_sine():
    rate = R.SAMPLE_RATE
    t = np.arange(rate) / rate
    ramp = np.minimum(t / 0.05, 1.0) * (t < 0.6)
    tone = 0.5 * np.sin(2 * np.pi * 440 * t) * ramp
    stats = R.analyze(np.vstack([tone, tone]), rate,
                      {"first_note_sec": 0, "first_note_len_sec": 0.5, "notes_end_sec": 0.6})
    assert stats["peak_dbfs"] == pytest.approx(-6.0, abs=0.2)
    assert stats["stereo_correlation"] == 1.0
    assert stats["band_energy_pct"]["low_mid"] > 99
    assert 400 < stats["spectral_centroid_hz"] < 480
    assert 35 <= stats["attack_ms"] <= 55
    assert stats["tail_sec"] < 0.05
    assert R.compare(stats, stats)["centroid_ratio"] == 1.0


def test_analyze_silence():
    stats = R.analyze(np.zeros((2, 1000)))
    assert stats["silent"] and "hint" in stats


def test_wav_roundtrip_and_unique_names(tmp_path, monkeypatch):
    monkeypatch.setattr(R.tempfile, "gettempdir", lambda: str(tmp_path))
    audio = np.vstack([np.linspace(-0.5, 0.5, 100)] * 2)
    first = R.write_wav(audio, R.output_path("Test"))
    second = R.output_path("Test")
    assert first != second and first.parent == second.parent
    back, rate = R.read_wav(first)
    assert rate == R.SAMPLE_RATE and np.allclose(back, audio, atol=1e-4)
    with pytest.raises(ValueError, match="wav"):
        R.output_path("Test", str(tmp_path / "x.mp3"))


def _can_render():
    if importlib.util.find_spec("dawdreamer") is None:
        return False
    try:
        R.plugin_path()
    except R.RenderError:
        return False
    return True


@pytest.mark.skipif(not _can_render(), reason="needs DawDreamer and Vital.vst3")
def test_render_real_vital():
    p = V.init_preset("Render Test")
    V.apply_parameters(p, {"filter_1_on": "on", "filter_1_cutoff": "300 Hz", "filter_1_resonance": "0%"})
    dark, _, info = R.render(p, "note", 120, 0.5)
    V.apply_parameters(p, {"filter_1_cutoff": "8 kHz"})
    bright, _, _ = R.render(p, "note", 120, 0.5)
    dark_stats, bright_stats = R.analyze(dark, info=info), R.analyze(bright, info=info)
    assert not dark_stats["silent"]
    assert bright_stats["spectral_centroid_hz"] > 2 * dark_stats["spectral_centroid_hz"]
