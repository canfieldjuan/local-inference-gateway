from __future__ import annotations

from pathlib import Path


def test_systemd_unit_encodes_single_process_private_state_boundary() -> None:
    unit_path = Path(__file__).parents[1] / "deploy" / "systemd" / "local-inference-gateway.service"
    unit = unit_path.read_text(encoding="utf-8")

    assert unit.count("ExecStart=") == 1
    assert "--workers" not in unit
    assert "EnvironmentFile=/etc/local-inference-gateway/gateway.env" in unit
    assert "StateDirectory=local-inference-gateway" in unit
    assert "StateDirectoryMode=0700" in unit
    assert "ReadWritePaths=/var/lib/local-inference-gateway" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "0.0.0.0" not in unit
    assert "GATEWAY_OLLAMA_URL" not in unit
