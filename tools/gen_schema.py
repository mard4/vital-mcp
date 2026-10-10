"""Generate vital_mcp/schema.json from Vital's open-source parameter table.

Downloads src/common/synth_parameters.cpp and synth_strings.h from
github.com/mtytel/vital (GPL-3.0) unless local copies are passed:

    python tools/gen_schema.py [synth_parameters.cpp synth_strings.h]
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com/mtytel/vital/main/src/"
OUT = Path(__file__).resolve().parent.parent / "vital_mcp" / "schema.json"

# Enum sizes referenced by the parameter table (from the Vital headers).
CONSTANTS = {
    "Distortion::kMinDrive": -30.0,
    "Distortion::kMaxDrive": 30.0,
    "DigitalSvf::kMinGain": -15.0,
    "DigitalSvf::kMaxGain": 15.0,
    "MultibandCompressor::kNumBandOptions": 4,
    "PredefinedWaveFrames::kNumShapes": 6,
    "RandomLfo::kNumStyles": 4,
    "SynthLfo::kNumSyncOptions": 5,
    "SynthLfo::kNumSyncTypes": 6,
    "SynthOscillator::kNumDistortionTypes": 13,
    "SynthOscillator::kNumSpectralMorphTypes": 12,
    "SynthOscillator::kNumUnisonStackTypes": 11,
    "VoiceHandler::kKill": 0,
    "VoiceHandler::kRoundRobin": 4,
    "VoiceHandler::kNumVoiceOverrides": 2,
    "VoiceHandler::kNumVoicePriorities": 5,
    "constants::kNumEffects": 9,
    "vital::kNumEffects": 9,
    "constants::kNumSourceDestinations": 5,
    "kNumFilterModels": 8,
    "kNumOscillatorWaveFrames": 257,
    "kMaxPolyphony": 33,
    "kDegreesPerCycle": 360.0,
    "vital::MultibandCompressor::kNumBandOptions": 4,
    "vital::utils::factorial(vital::kNumEffects)": 362880,  # 9!
}

GROUPS = {
    "parameter_list": None,
    "env_parameter_list": ("env", [str(i) for i in range(1, 7)]),
    "lfo_parameter_list": ("lfo", [str(i) for i in range(1, 9)]),
    "random_lfo_parameter_list": ("random", [str(i) for i in range(1, 5)]),
    "filter_parameter_list": ("filter", ["1", "2", "fx"]),
    "osc_parameter_list": ("osc", ["1", "2", "3"]),
    "mod_parameter_list": ("modulation", [str(i) for i in range(1, 65)]),
}

ENTRY = re.compile(
    r'\{\s*"(?P<name>[a-z0-9_]+)"\s*,\s*(?P<version>0x[0-9a-fA-F]+)\s*,'
    r'\s*(?P<min>[^,]+),\s*(?P<max>[^,]+),\s*(?P<default>[^,]+),'
    r'\s*(?P<post>[^,]+),\s*(?P<mult>[^,]+),\s*ValueDetails::k(?P<scale>\w+)\s*,'
    r'\s*(?P<invert>true|false)\s*,\s*"(?P<units>[^"]*)"\s*,\s*"(?P<display>[^"]*)"\s*,'
    r'\s*(?:strings::(?P<strings>\w+)|nullptr)\s*\}',
    re.S,
)


def fetch(path):
    with urllib.request.urlopen(RAW + path) as response:
        return response.read().decode()


def evaluate(expr):
    expr = expr.strip().rstrip("f")
    for name, value in sorted(CONSTANTS.items(), key=lambda kv: -len(kv[0])):
        expr = expr.replace(name, repr(value))
    if not re.fullmatch(r"[0-9eE.+\-*/() ]+", expr):
        raise ValueError(f"unresolved expression: {expr}")
    return float(eval(expr))


def parse_strings(source):
    tables = {}
    for match in re.finditer(r"const std::string (k\w+)\[[^\]]*\]\s*=\s*\{(.*?)\};", source, re.S):
        tables[match.group(1)] = re.findall(r'"([^"]*)"', match.group(2))
    return tables


def parse_entries(block, strings):
    entries = []
    for m in ENTRY.finditer(block):
        entry = {
            "name": m["name"],
            "min": evaluate(m["min"]),
            "max": evaluate(m["max"]),
            "default": evaluate(m["default"]),
            "post_offset": evaluate(m["post"]),
            "display_multiply": evaluate(m["mult"]),
            "scale": m["scale"].lower(),
            "display_invert": m["invert"] == "true",
            "units": m["units"].strip(),
            "display_name": m["display"],
        }
        if m["strings"]:
            entry["options"] = strings[m["strings"]]
        entries.append(entry)
    return entries


def main():
    if len(sys.argv) == 3:
        params_src = Path(sys.argv[1]).read_text()
        strings_src = Path(sys.argv[2]).read_text()
    else:
        params_src = fetch("common/synth_parameters.cpp")
        strings_src = fetch("interface/look_and_feel/synth_strings.h")

    strings = parse_strings(strings_src)
    schema = {}
    for list_name, group in GROUPS.items():
        match = re.search(rf"ValueDetailsLookup::{list_name}\[\]\s*=\s*\{{(.*?)\n  \}};", params_src, re.S)
        if not match:
            raise SystemExit(f"missing table {list_name}")
        entries = parse_entries(match.group(1), strings)
        expected = match.group(1).count("ValueDetails::k")
        if len(entries) != expected:
            raise SystemExit(f"{list_name}: parsed {len(entries)} of {expected} entries")
        if group is None:
            for e in entries:
                schema[e["name"]] = e
            continue
        prefix, ids = group
        for index in ids:
            for e in entries:
                e = dict(e)
                e["name"] = f"{prefix}_{index}_{e['name']}"
                schema[e["name"]] = e

    # Overrides applied in ValueDetailsLookup's constructor.
    for name, value in {"osc_1_on": 1.0, "osc_2_destination": 1.0, "osc_3_destination": 3.0,
                        "filter_1_osc1_input": 1.0, "filter_2_osc2_input": 1.0}.items():
        if name in schema:
            schema[name]["default"] = value

    OUT.write_text(json.dumps(schema, indent=1, sort_keys=True))
    print(f"wrote {len(schema)} parameters to {OUT}")


if __name__ == "__main__":
    main()
