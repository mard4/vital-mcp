import json
from pathlib import Path

import numpy as np
import pytest

from vital_mcp import params as P
from vital_mcp import preset as V
from vital_mcp import wavetable as W

REAL_PRESETS = sorted((Path.home() / "Music/Vital").rglob("*.vital"))


@pytest.fixture
def vital_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VITAL_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("name,value,display", [
    ("filter_1_cutoff", "800 Hz", "800 Hz"),
    ("filter_1_cutoff", "2.5 kHz", "2500 Hz"),
    ("env_1_attack", "250 ms", "250 ms"),
    ("env_1_release", 2, "2 s"),
    ("lfo_1_frequency", "0.5 s", "500 ms"),
    ("reverb_dry_wet", "30%", "30%"),
    ("reverb_dry_wet", 0.3, "30%"),
    ("filter_1_model", "Ladder", "Ladder"),
    ("delay_tempo", "1/8", "1/8"),
    ("chorus_on", "on", "On"),
    ("osc_1_transpose", -12, "-12"),
    ("volume", "-6 dB", "-6 dB"),
    ("chorus_cutoff", "1 kHz", "1000 Hz"),
])
def test_round_trip(name, value, display):
    raw, warning = P.from_user(name, value)
    assert warning is None
    assert P.to_display(name, raw) == display


def test_filter_style_names_win_over_units():
    settings = {"filter_1_model": 2.0}
    assert P.from_user("filter_1_style", "24dB", settings)[0] == 1.0
    settings = {"filter_1_model": 6.0}  # comb
    assert P.from_user("filter_1_style", "Band Spread Comb", settings)[0] == 3.0


def test_out_of_range_is_clamped_with_warning():
    raw, warning = P.from_user("env_1_attack", 100)
    assert raw == P.details("env_1_attack")["max"]
    assert "clamped" in warning


def test_unknown_parameter_suggests():
    with pytest.raises(P.ParameterError, match="filter_1_cutoff"):
        P.details("filter_1_cuttof")


def test_wavetable_encoding():
    table = W.build("T", ["saw", {"harmonics": [1, 0.5]}, "square"])
    keyframes = table["groups"][0]["components"][0]["keyframes"]
    assert [k["position"] for k in keyframes] == [0, 128, 256]
    wave = W.decode(keyframes[0]["wave_data"])
    assert wave.shape == (W.FRAME_SIZE,)
    assert np.isclose(np.max(np.abs(wave)), 1.0, atol=1e-3)


def test_create_edit_compare(vital_dir):
    p = V.init_preset("Test")
    V.set_metadata(p, style="Pad", macro_names={"1": "TONE"})
    V.apply_parameters(p, {"filter_1_on": "on", "filter_1_cutoff": "1 kHz"})
    V.set_modulations(p, [{"source": "macro_1", "destination": "filter_1_cutoff", "amount": 0.4}])
    V.set_wavetable(p, 1, {"morph": ["sine", "saw"], "steps": 4})
    V.set_lfo(p, 1, "square")
    path = V.save(p, V.output_dir() / "Test.vital")

    text = path.read_text()
    assert text.startswith('{"author":"')
    loaded, _ = V.load("Test")
    assert loaded["preset_style"] == "Pad"
    assert V.active_modulations(loaded["settings"])[0]["source"] == "macro_control_1"
    assert "TONE" in V.describe(loaded)

    V.set_modulations(loaded, [{"source": "macro_control_1", "destination": "filter_1_cutoff", "remove": True}])
    assert V.active_modulations(loaded["settings"]) == []
    changes = V.diff(p, loaded)
    assert any("macro_control_1" in c for c in changes)

    with pytest.raises(FileExistsError):
        V.save(p, path)
    V.save(p, path, overwrite=True)
    assert list((vital_dir / "User/.vital-mcp-backups").glob("Test.*.vital"))


def test_modulation_validation():
    p = V.init_preset()
    with pytest.raises(ValueError):
        V.set_modulations(p, [{"source": "lfo_9", "destination": "filter_1_cutoff"}])
    with pytest.raises(ValueError):
        V.set_modulations(p, [{"source": "lfo_1", "destination": "filter_1_model"}])


@pytest.mark.skipif(not REAL_PRESETS, reason="no local Vital presets")
@pytest.mark.parametrize("path", REAL_PRESETS, ids=lambda p: p.stem)
def test_describes_real_presets(path):
    data = json.loads(path.read_text())
    assert V.describe(data).startswith("# ")
    assert V.get_parameters(data, non_default_only=True) is not None
