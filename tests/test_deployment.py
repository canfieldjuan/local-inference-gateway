from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def test_systemd_unit_encodes_single_process_private_state_boundary() -> None:
    unit_path = ROOT / "deploy" / "systemd" / "local-inference-gateway.service"
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


def test_systemd_installer_is_syntax_valid_and_refuses_non_root() -> None:
    installer = ROOT / "deploy" / "systemd" / "install.sh"

    assert installer.stat().st_mode & 0o111
    subprocess.run(["bash", "-n", str(installer)], check=True)
    if os.geteuid() == 0:
        pytest.skip("non-root admission is exercised only by an unprivileged test process")

    result = subprocess.run(
        ["bash", str(installer)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "error: run this installer as root\n"


def test_systemd_installer_activates_only_complete_clean_revision() -> None:
    installer = (ROOT / "deploy" / "systemd" / "install.sh").read_text(encoding="utf-8")

    assert "status --porcelain --untracked-files=all" in installer
    assert "rev-parse --verify 'HEAD^{commit}'" in installer
    assert 'release_root="$install_root/releases"' in installer
    assert '"$uv_bin" export' in installer
    assert "--locked" in installer
    assert "--no-dev" in installer
    assert 'install -d -o root -g root -m 0755 "$release_dir"' in installer
    assert '"$uv_bin" venv "$release_dir/venv"' in installer
    assert '--python "$release_dir/venv/bin/python"' in installer
    assert '[[ -x "$release_executable" ]]' in installer
    assert 'release_marker="$release_dir/SOURCE_REVISION"' in installer
    assert '[[ ! -L "$release_dir" ]]' in installer
    assert '-f "$release_marker"' in installer
    assert '[[ "$(<"$release_marker")" == "$source_revision" ]]' in installer
    assert 'printf \'%s\\n\' "$source_revision" >"$release_marker"' in installer
    assert "release_created=true" in installer
    assert '[[ "$release_created" == true' in installer
    assert 'ln -s -- "$release_dir/venv" "$temporary_link"' in installer
    assert 'mv -Tf -- "$temporary_link" "$active_venv"' in installer
    assert installer.index('[[ -x "$release_executable" ]]') < installer.index(
        'ln -s -- "$release_dir/venv"'
    )
    assert installer.index('if [[ -e "$active_venv"') < installer.index("install -d")


def test_systemd_installer_neither_provisions_secrets_nor_activates_service() -> None:
    installer = (ROOT / "deploy" / "systemd" / "install.sh").read_text(encoding="utf-8")

    assert "GATEWAY_" not in installer
    assert "/home/" not in installer
    assert "openssl" not in installer
    assert "token_hex" not in installer
    assert "systemctl daemon-reload" in installer
    assert not re.search(
        r"systemctl\s+(?:--[^ ]+\s+)*(?:start|stop|restart|enable|disable)\b",
        installer,
    )
