"""Build the Claude Desktop extensions into dist/:

  vital-mcp.mcpb          presets only (small)
  vital-mcp-render.mcpb   presets + render_preset (installs DawDreamer, ~130 MB)

Both share the extension name, so installing one replaces the other.
Run from the repo root: uv run python tools/build_mcpb.py
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
MCPB = ["npx", "-y", "@anthropic-ai/mcpb@2.1.2"]


def pack(source, out):
    subprocess.run(MCPB + ["validate", str(source / "manifest.json")], check=True)
    subprocess.run(MCPB + ["pack", str(source), str(out)], check=True)


def render_manifest(manifest):
    manifest = json.loads(json.dumps(manifest))
    manifest["display_name"] += " (with rendering)"
    manifest["description"] = "Design, edit, explain and listen to presets for the Vital wavetable synth."
    manifest["long_description"] = manifest["long_description"].split(" To also render")[0] + (
        " Presets can also be rendered offline through the real Vital plugin (its VST3 must be installed)"
        " and analyzed: levels, tonal balance, stereo width, attack and tail. Installs DawDreamer (~130 MB).")
    config = manifest["server"]["mcp_config"]
    config["args"] = config["args"][:-1] + ["--extra", "render", config["args"][-1]]
    config["env"]["VITAL_PLUGIN_PATH"] = "${user_config.vital_plugin_path}"
    manifest["user_config"]["vital_plugin_path"] = {
        "type": "file",
        "title": "Vital.vst3 (optional)",
        "description": "Only needed if Vital's VST3 isn't in the standard plugin folder.",
        "required": False,
    }
    for tool in manifest["tools"]:
        if tool["name"] == "render_preset":
            tool["description"] = tool["description"].split(" (needs")[0]
    return manifest


def main():
    DIST.mkdir(exist_ok=True)
    pack(ROOT, DIST / "vital-mcp.mcpb")
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "vital-mcp"
        shutil.copytree(ROOT, stage, ignore=shutil.ignore_patterns(
            ".git", ".venv", "dist", "__pycache__", ".pytest_cache", "*.mcpb"))
        manifest = json.loads((ROOT / "manifest.json").read_text())
        (stage / "manifest.json").write_text(json.dumps(render_manifest(manifest), indent=2) + "\n")
        pack(stage, DIST / "vital-mcp-render.mcpb")
    print(f"Built {DIST / 'vital-mcp.mcpb'} and {DIST / 'vital-mcp-render.mcpb'}")


if __name__ == "__main__":
    main()
