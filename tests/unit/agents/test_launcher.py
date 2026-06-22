from __future__ import annotations

import pytest

from mcbench.agents.launcher import detect_launch


def test_detect_launch_for_node_directory(tmp_path) -> None:
    (tmp_path / "package.json").write_text("{}")
    assert detect_launch(tmp_path) == ["node", "index.js"]


def test_detect_launch_for_python_directory(tmp_path) -> None:
    (tmp_path / "main.py").write_text("print('x')\n")
    assert detect_launch(tmp_path) == ["python3", "main.py"]


def test_detect_launch_for_executable_file(tmp_path) -> None:
    executable = tmp_path / "agent.sh"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    assert detect_launch(executable) == [str(executable)]


def test_detect_launch_raises_for_unknown_path(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        detect_launch(tmp_path)
