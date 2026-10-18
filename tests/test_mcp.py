"""Verify generated Tools, Resource and Prompt through the official MCP client."""

import asyncio
import importlib.util
import json
from pathlib import Path
import sys

from mcp import Client

from personal_agent_builder.generator import generate, proposal_hash

ROOT = Path(__file__).resolve().parents[1]


def test_generated_mcp_server_calls_real_python(tmp_path):
    proposal = json.loads((ROOT / "examples" / "csv_proposal.json").read_text(encoding="utf-8"))
    bundle = generate(ROOT / "examples" / "csv_project", proposal,
                      {"approved_by_user": True, "proposal_sha256": proposal_hash(proposal), "note": "test fixture only"},
                      tmp_path / "agent")
    sys.path.insert(0, str(bundle))
    try:
        spec = importlib.util.spec_from_file_location("generated_mcp_smoke", bundle / "mcp_server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        async def run():
            async with Client(module.mcp, raise_exceptions=True) as client:
                listed = await client.list_tools()
                assert "summarize_csv" in [tool.name for tool in listed.tools]
                result = await client.call_tool("summarize_csv", {})
                assert not result.is_error
                record = result.structured_content or json.loads(result.content[0].text)
                assert record["exit_code"] == 0
                assert record["verified_output"] is True
                resources = await client.list_resources()
                assert any(str(r.uri) == "agent://capabilities" for r in resources.resources)
                prompts = await client.list_prompts()
                assert any(p.name == "usage" for p in prompts.prompts)

        asyncio.run(run())
        assert json.loads((bundle / "project" / "summary.json").read_text(encoding="utf-8"))["sum"] == "10"
    finally:
        sys.path.remove(str(bundle))


def test_seismic_mcp_required_parameters_after_defaults(tmp_path):
    proposal = json.loads((ROOT / "examples" / "seismic_proposal.json").read_text(encoding="utf-8"))
    bundle = generate(ROOT / "examples" / "seismic_v1_snapshot", proposal,
                      {"approved_by_user": True, "proposal_sha256": proposal_hash(proposal), "note": "test fixture only"},
                      tmp_path / "seismic_agent")
    sys.path.insert(0, str(bundle))
    try:
        spec = importlib.util.spec_from_file_location("generated_seismic_mcp_smoke", bundle / "mcp_server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        async def run():
            async with Client(module.mcp, raise_exceptions=True) as client:
                listed = await client.list_tools()
                assert {tool.name for tool in listed.tools} == {"preview_model", "move_anomaly"}
                result = await client.call_tool("move_anomaly", {"x": 35, "z": 20})
                assert not result.is_error
                record = result.structured_content or json.loads(result.content[0].text)
                assert record["exit_code"] == 0
                assert record["verified_output"] is True

        asyncio.run(run())
        assert (bundle / "project" / "modified_config.json").is_file()
    finally:
        sys.path.remove(str(bundle))
