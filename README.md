# vital-mcp

An MCP server that lets AI assistants (any MCP client) design, edit and explain presets for
the free [Vital](https://vital.audio) wavetable synth.

Vital presets are plain JSON, and Vital's source is open, so every parameter name, range
and unit here comes straight from Vital's own parameter table. No reverse engineering.

> "Make a dark reese bass with two detuned saws, a ladder filter at 400 Hz and a slow LFO on the cutoff. Put the LFO rate on macro 1."

## Tools

| Tool | What it does |
|---|---|
| `list_presets` | Search the presets in your Vital folder |
| `describe_preset` | Readable breakdown: oscillators, wavetables, filters, envelopes, LFOs, mod matrix, macros, FX |
| `get_preset_parameters` | Exact values in display units (Hz, ms, dB, %) |
| `list_parameters` | Look up parameter names, ranges, defaults and menu options |
| `create_preset` | Build a preset from the init patch or an existing preset |
| `edit_preset` | Change parameters, modulations, wavetables or LFO shapes, in place (with backup) or as a copy |
| `compare_presets` | Diff two presets |

### What you can set
- **All ~800 parameters** in friendly units: `"filter_1_cutoff": "1.2 kHz"`, `"env_1_release": "800 ms"`,
  `"reverb_dry_wet": "25%"`, `"filter_1_model": "Ladder"`, `"lfo_1_tempo": "1/8"`.
- **Modulation matrix**: `{"source": "lfo_1", "destination": "filter_1_cutoff", "amount": 0.35}`.
- **Wavetables**: basic shapes, additive harmonics, mixes, drive/fold, and morphing tables
  (`{"morph": ["saw", {"shape": "square", "drive": 3}], "steps": 8}`).
- **LFO shapes**: sine, triangle, saw, square, ramps or custom points with curve powers.
- **Metadata**: name, style, author, comments, macro labels.

Values outside a parameter's range are clamped and reported, and typos get suggestions.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Vital 1.5 or newer.

```bash
git clone <this repo> vital-mcp
```

Add it to any MCP client as a stdio server. The usual JSON config looks like this:

```json
{
  "mcpServers": {
    "vital": {
      "command": "uv",
      "args": ["--directory", "/path/to/vital-mcp", "run", "vital-mcp"]
    }
  }
}
```

## Where files go

- Presets are read from Vital's data folder (from `Vital.config`, usually `~/Music/Vital`
  on macOS and `Documents/Vital` on Windows). Override with `VITAL_DATA_DIR`.
- New presets are saved to `User/Presets/MCP/`. Open them from Vital's preset browser.
  After an edit, reselect the preset in Vital to reload it.
- Overwritten presets are backed up to `User/.vital-mcp-backups/`.

## Limits

- This writes files. It doesn't control a running Vital instance, so there's no live
  knob-turning and no audio rendering. Pair it with a DAW MCP (for example an Ableton MCP)
  to load and audition patches.
- Samples (the SMP oscillator) and the wavetable editor's file and line sources are kept
  as they are but can't be generated.

## Development

```bash
uv run pytest            # tests; also parses every preset in your Vital folder
uv run python tools/gen_schema.py   # regenerate schema.json from Vital's GitHub source
```

## License

GPL-3.0-or-later. `vital_mcp/schema.json` is derived from Vital's GPL-3.0 source
(github.com/mtytel/vital). Not affiliated with or endorsed by Vital Audio.
