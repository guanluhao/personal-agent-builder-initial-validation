import json
from pathlib import Path
import subprocess
import sys

import pytest

from examples.seismic_v1_snapshot.extend_tunnel import extend_tunnel
from examples.seismic_v1_snapshot.shift_anomaly_meters import shift_anomaly
from personal_agent_builder.analysis import inspect_project
from personal_agent_builder.generator import generate, proposal_hash
from personal_agent_builder.proposal import propose_tools
from personal_agent_builder.runtime import invoke


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "seismic_v1_snapshot"


def test_builder_discovers_metre_tools_without_special_case():
    analysis = inspect_project(SOURCE)
    proposal, samples = propose_tools(SOURCE, analysis, ["shift_anomaly_meters", "extend_tunnel"])
    groups = {tool["name"]: tool["context_group"] for tool in proposal["tools"]}
    assert groups == {"shift_anomaly_meters": "source:config.json",
                      "extend_tunnel": "source:tunnel_config.json"}
    assert samples["shift_anomaly_meters"]["source"] == "config.json"
    assert samples["extend_tunnel"]["source"] == "tunnel_config.json"


def test_shift_uses_metres_and_preserves_source(tmp_path):
    output = tmp_path / "shifted.json"
    result = shift_anomaly(SOURCE / "config.json", output, 5, 4)
    assert result["old_xyz_m"] == [30, 0, 24]
    assert result["new_xyz_m"] == [25, 0, 28]
    assert json.loads(output.read_text(encoding="utf-8"))["regions"][0]["index_bounds_zyx"] == [[28, 29], [0, 1], [25, 26]]
    assert json.loads((SOURCE / "config.json").read_text(encoding="utf-8"))["regions"][0]["index_bounds_zyx"] == [[24, 25], [0, 1], [30, 31]]
    with pytest.raises(ValueError, match="超出"):
        shift_anomaly(SOURCE / "config.json", tmp_path / "outside.json", 31, 0)
    coarse = json.loads((SOURCE / "config.json").read_text(encoding="utf-8"))
    coarse["grid"]["spacing_zyx_m"][2] = 2
    coarse_path = tmp_path / "coarse.json"
    coarse_path.write_text(json.dumps(coarse), encoding="utf-8")
    with pytest.raises(ValueError, match="网格"):
        shift_anomaly(coarse_path, tmp_path / "off_grid.json", 5, 0)

    script = "from seismic.model import build; build(__import__('sys').argv[1], __import__('sys').argv[2])"
    for config, case in ((SOURCE / "config.json", tmp_path / "before_case"),
                         (output, tmp_path / "after_case")):
        run = subprocess.run([sys.executable, "-c", script, str(config), str(case)],
                             cwd=SOURCE, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr
    from examples.seismic_v1_snapshot.seismic.dds import read
    old_rho = read(tmp_path / "before_case" / "rho")[0]
    new_rho = read(tmp_path / "after_case" / "rho")[0]
    assert (old_rho != new_rho).sum() == 2
    assert new_rho[28, 0, 25] == pytest.approx(1.5)


def test_extend_tunnel_changes_real_v1_model(tmp_path):
    before = SOURCE / "tunnel_config.json"
    after = tmp_path / "tunnel_after.json"
    result = extend_tunnel(before, after, 10)
    assert result["old_length_m"] == 10
    assert result["new_length_m"] == 20
    assert json.loads(before.read_text(encoding="utf-8"))["regions"][0]["bounds_xyz_m"][0] == [20, 30]
    assert json.loads(after.read_text(encoding="utf-8"))["regions"][0]["bounds_xyz_m"][0] == [20, 40]
    with pytest.raises(ValueError, match="超出"):
        extend_tunnel(before, tmp_path / "outside.json", 40)

    script = "from seismic.model import build; build(__import__('sys').argv[1], __import__('sys').argv[2])"
    for config, case in ((before, tmp_path / "before_case"), (after, tmp_path / "after_case")):
        run = subprocess.run([sys.executable, "-c", script, str(config), str(case)],
                             cwd=SOURCE, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr
    from examples.seismic_v1_snapshot.seismic.dds import read
    old_vp = read(tmp_path / "before_case" / "vp")[0]
    new_vp = read(tmp_path / "after_case" / "vp")[0]
    assert (old_vp != new_vp).sum() == 50
    assert (new_vp[20:25, 0, 30:40] == 0).all()


def test_generated_agent_keeps_tunnel_and_anomaly_context_separate(tmp_path):
    proposal = json.loads((ROOT / "examples" / "seismic_natural_proposal.json").read_text(encoding="utf-8"))
    bundle = generate(SOURCE, proposal,
                      {"approved_by_user": True, "proposal_sha256": proposal_hash(proposal), "note": "test"},
                      tmp_path / "natural-agent")
    (bundle / "router.py").write_text(
        "def route(message, manifest, context):\n"
        "    return {'action': 'invoke', 'tool': 'shift_anomaly_meters', "
        "'parameters': {'left_m': 5, 'down_m': 4}}\n", encoding="utf-8")
    first = subprocess.run([sys.executable, str(bundle / "agent.py"), "shift_anomaly_meters left_m=5 down_m=4"],
                           capture_output=True, text=True, timeout=30)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["verified_output"] is True
    (bundle / "router.py").write_text(
        "def route(message, manifest, context):\n"
        "    return {'action': 'invoke', 'tool': 'extend_tunnel', 'parameters': {'meters': 10}}\n",
        encoding="utf-8")
    second = subprocess.run([sys.executable, str(bundle / "agent.py"), "extend_tunnel meters=10"],
                            capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    result = json.loads(second.stdout)
    assert result["verified_output"] is True
    assert result["params"]["source"].endswith("tunnel_config.json")
    assert json.loads(result["stdout"])["new_length_m"] == 20
    context = json.loads((bundle / "records" / "context.json").read_text(encoding="utf-8"))
    assert context["latest_config_by_group"] == {
        "density_anomaly": "shifted_config.json", "tunnel": "extended_tunnel_config.json"}
    with pytest.raises(ValueError, match="范围"):
        invoke(bundle, "extend_tunnel", {"meters": 31})
