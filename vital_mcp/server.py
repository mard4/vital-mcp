"""MCP server for designing and editing Vital synth presets."""

import functools
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import params as P
from . import preset as V

INSTRUCTIONS = """
Design, edit and inspect presets for the Vital wavetable synth by writing .vital files.
Vital is not controlled live: after saving, the user opens the preset from Vital's
browser (User > Presets > MCP by default) or re-selects it to reload changes.

Sound-design notes:
- Parameter values are in display units: cutoffs in Hz ("800 Hz", "2.5 kHz"), times
  in seconds or "250 ms", levels/mix in % ("30%"), gains in dB, menus by option name
  ("Ladder", "1/8", "Tempo"). Use list_parameters to look up names and ranges.
- env_1 is hard-wired to amplitude. Use env_2..6 for filter/pitch/etc. via modulations.
- Filters: osc_N_destination routes oscillators ("Filter 1", "Filter 2", "Filter 1+2",
  "Effects", "Direct Out"). For Analog/Dirty/Ladder/Digital 12dB/24dB styles,
  filter_N_blend 0 = low-pass, 1 = band-pass, 2 = high-pass. Turn filters on with filter_N_on.
- Modulation amount is a fraction of the destination's full range (-1..1). Filter cutoff
  spans ~8 Hz..21 kHz, so +0.3 is roughly +3 octaves.
- LFO rate: lfo_N_sync ("Seconds", "Tempo", ...), then lfo_N_tempo ("1/4") or
  lfo_N_frequency (seconds per cycle). lfo_N_sync_type "Envelope" makes it one-shot.
- Effects each have <fx>_on plus parameters: chorus, compressor, delay, distortion, eq,
  filter_fx, flanger, phaser, reverb. Wet amounts are <fx>_dry_wet or <fx>_mix.
- Unison: osc_N_unison_voices (1-16), osc_N_unison_detune, osc_N_stereo_spread.
- Name macros (macro_names) and route macro_control_N to the main expressive targets.
- Keep volume around -6 dB to -3 dB; big unison stacks and resonance get loud.
"""

mcp = MCPServer("vital", instructions=INSTRUCTIONS)


def tool(fn):
    """Register a tool, passing expected errors (bad names, values, paths) back to the model."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, KeyError, OSError) as exc:
            raise ToolError(str(exc)) from exc
    return mcp.tool()(wrapper)


def _apply_changes(preset, parameters, modulations, clear_modulations, wavetables, lfos):
    report = []
    warnings = []
    if wavetables:
        for osc, spec in wavetables.items():
            report.append(f"osc {osc} wavetable: {V.set_wavetable(preset, int(osc), spec)}")
    if lfos:
        for number, spec in lfos.items():
            V.set_lfo(preset, int(number), spec)
            report.append(f"lfo {number} shape: {spec if isinstance(spec, str) else 'custom'}")
    if parameters:
        changes, warns = V.apply_parameters(preset, parameters)
        report += changes
        warnings += warns
    if modulations or clear_modulations:
        report += V.set_modulations(preset, modulations or [], clear_modulations)
    return report, warnings


def _result(path, preset, report, warnings):
    lines = [f"Saved: {path}", ""]
    if warnings:
        lines += ["Warnings:"] + [f"- {w}" for w in warnings] + [""]
    if report:
        lines += ["Changes:"] + [f"- {r}" for r in report] + [""]
    lines.append(V.describe(preset))
    return "\n".join(lines)


@tool
def list_presets(query: str = "", limit: int = 40) -> str:
    """Find .vital presets in the user's Vital folder. query matches name, folder, style, author."""
    paths = V.find_presets(query, limit)
    if not paths:
        return f"No presets found in {V.data_dir()}" + (f" matching '{query}'" if query else "")
    root = V.data_dir()
    return f"{len(paths)} preset(s) in {root}:\n" + "\n".join(f"- {p.relative_to(root)}" for p in paths)


@tool
def describe_preset(preset: str) -> str:
    """Readable summary of a preset: oscillators, wavetables, filters, envelopes, LFOs,
    modulation routings, macros and active effects. preset = name or path."""
    data, path = V.load(preset)
    return f"File: {path}\n\n" + V.describe(data)


@tool
def get_preset_parameters(preset: str, names: list[str] | None = None, prefix: str | None = None,
                          non_default_only: bool = True) -> dict[str, str]:
    """Exact parameter values of a preset in display units. Filter by names or a prefix
    such as 'osc_1_', 'filter_2_', 'reverb_'. By default only values that differ from Vital's defaults."""
    data, _ = V.load(preset)
    return V.get_parameters(data, names, prefix, non_default_only and not names)


@tool
def list_parameters(prefix: str = "", search: str = "") -> Any:
    """Look up Vital parameter names, ranges, units, defaults and menu options.
    Use prefix (e.g. 'osc_1_', 'filter_1_', 'env_2_', 'lfo_1_', 'reverb_') or search text.
    With no arguments, returns the parameter sections."""
    names = sorted(P.schema())
    if not prefix and not search:
        sections = {}
        for name in names:
            parts = name.split("_") + [""]
            key = "_".join(parts[:2]) + "_" if parts[1].isdigit() or parts[1] == "fx" else parts[0] + "_"
            if key.startswith("modulation_"):
                key = "modulation_N_"
            sections[key] = sections.get(key, 0) + 1
        return {"sections (use as prefix)": sections,
                "note": "Groups repeat per index: osc_1..3, filter_1/2/fx, env_1..6, lfo_1..8, random_1..4"}
    words = search.lower().split()
    matches = [n for n in names if n.startswith(prefix)
               and all(w in (n + " " + P.details(n)["display_name"]).lower() for w in words)]
    if len(matches) > 120:
        return {"error": f"{len(matches)} matches, narrow the prefix or search", "sample": matches[:40]}
    return [P.describe_parameter(n) for n in matches]


@tool
def create_preset(
    name: str,
    parameters: dict[str, Any] | None = None,
    modulations: list[dict[str, Any]] | None = None,
    wavetables: dict[str, Any] | None = None,
    lfos: dict[str, Any] | None = None,
    style: str | None = None,
    author: str | None = None,
    comments: str | None = None,
    macro_names: dict[str, str] | None = None,
    base_preset: str | None = None,
    subfolder: str = "MCP",
    overwrite: bool = False,
) -> str:
    """Create and save a new Vital preset, starting from Vital's init patch or base_preset.

    parameters: {"osc_1_unison_voices": 7, "filter_1_on": "on", "filter_1_cutoff": "1.2 kHz",
                 "env_1_release": "800 ms", "reverb_on": true, "reverb_dry_wet": "25%"}
    modulations: [{"source": "env_2", "destination": "filter_1_cutoff", "amount": 0.35},
                  {"source": "lfo_1", "destination": "osc_1_wave_frame", "amount": 0.5, "bipolar": true}]
      sources: env_1-6, lfo_1-8, random_1-4, macro_control_1-4, velocity, note, aftertouch, mod_wheel, ...
    wavetables: {"1": "saw"} | {"2": {"frames": ["sine", {"harmonics": [1, 0, 0.3]}, "square"],
                 "interpolation": "spectral", "name": "Sine to Square"}}
      | {"1": {"morph": [{"shape": "pulse", "width": 0.5}, {"shape": "pulse", "width": 0.05}], "steps": 16}}
      frame specs: shape name (sine, saw, ramp, square, triangle, pulse, noise), {"shape", "width"},
      {"harmonics": [amps]}, {"samples": [...]}, {"mix": [...], "weights": [...]}, plus "drive"/"fold".
    lfos: {"1": "sine"} | {"2": {"points": [[0, 0], [0.1, 1], [1, 0]], "powers": [0, -3, 0]}}
      shapes: triangle, sine, saw_up, saw_down, square, ramp_up, ramp_down, constant (y=1 is top).
    style: Bass, Lead, Keys, Pad, Percussion, Sequence, Experiment, SFX, Template.
    macro_names: {"1": "CUTOFF", "2": "SPACE"}.
    """
    if base_preset:
        data, _ = V.load(base_preset)
        data = V.clone(data)
    else:
        data = V.init_preset(name)
    V.set_metadata(data, name=name, author=author, style=style, comments=comments, macro_names=macro_names)
    report, warnings = _apply_changes(data, parameters, modulations, False, wavetables, lfos)
    path = V.save(data, V.output_dir(subfolder) / f"{V.safe_filename(name)}.vital", overwrite)
    return _result(path, data, report, warnings)


@tool
def edit_preset(
    preset: str,
    parameters: dict[str, Any] | None = None,
    modulations: list[dict[str, Any]] | None = None,
    clear_modulations: bool = False,
    wavetables: dict[str, Any] | None = None,
    lfos: dict[str, Any] | None = None,
    style: str | None = None,
    author: str | None = None,
    comments: str | None = None,
    macro_names: dict[str, str] | None = None,
    save_as: str | None = None,
    subfolder: str = "MCP",
) -> str:
    """Modify an existing preset. Same argument formats as create_preset.

    modulations update matching source->destination pairs, add new ones, or delete with
    {"source": ..., "destination": ..., "remove": true}. clear_modulations wipes all first.
    save_as: new preset name saved in the MCP subfolder (original untouched). Without it
    the original file is overwritten and a backup goes to User/.vital-mcp-backups.
    """
    data, path = V.load(preset)
    V.set_metadata(data, name=save_as, author=author, style=style, comments=comments, macro_names=macro_names)
    report, warnings = _apply_changes(data, parameters, modulations, clear_modulations, wavetables, lfos)
    if save_as:
        target = V.output_dir(subfolder) / f"{V.safe_filename(save_as)}.vital"
        saved = V.save(data, target, overwrite=False)
    else:
        saved = V.save(data, path, overwrite=True)
    return _result(saved, data, report, warnings)


@tool
def compare_presets(preset_a: str, preset_b: str) -> str:
    """List every parameter, modulation and wavetable difference between two presets."""
    a, path_a = V.load(preset_a)
    b, path_b = V.load(preset_b)
    changes = V.diff(a, b)
    header = f"A: {path_a}\nB: {path_b}\n{len(changes)} difference(s)"
    return header + ("\n" + "\n".join(f"- {c}" for c in changes) if changes else "")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
