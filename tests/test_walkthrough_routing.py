"""Pure orthogonal connector geometry for responsive walkthrough diagrams."""
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_walkthrough_connector_geometry():
    result = subprocess.run(
        ["node", "--test", "tests/test_walkthrough_routing.cjs"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
