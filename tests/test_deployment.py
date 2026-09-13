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
    build_constraints = (ROOT / "deploy" / "systemd" / "build-constraints.txt").read_text(
        encoding="utf-8"
    )

    assert "status --porcelain --untracked-files=all" in installer
    assert "rev-parse --verify 'HEAD^{commit}'" in installer
    assert 'release_root="$install_root/releases"' in installer
    assert 'export PATH="/usr/sbin:/usr/bin:/sbin:/bin"' in installer
    assert 'resolved_uv="$(readlink -f -- "$uv_bin")"' in installer
    assert 'validate_root_owned_nonwritable_path "$resolved_uv"' in installer
    assert "uv_command()" in installer
    assert 'env -i HOME=/root PATH="$PATH" "$uv_bin" --no-config "$@"' in installer
    assert "uv_command export" in installer
    assert "uv_command venv" in installer
    assert "uv_command pip install" in installer
    assert "--locked" in installer
    assert "--no-dev" in installer
    assert 'install -d -o root -g root -m 0755 "$release_dir"' in installer
    assert 'uv_command venv --python "$python_bin" "$release_dir/venv"' in installer
    assert '--python "$release_dir/venv/bin/python"' in installer
    assert "--link-mode copy" in installer
    assert '--build-constraints "$build_constraints_file"' in installer
    assert (
        '[[ -x "$release_executable" && -f "$release_executable" && '
        '! -L "$release_executable" ]]' in installer
    )
    assert 'release_marker="$release_dir/SOURCE_REVISION"' in installer
    assert '[[ ! -L "$release_dir" ]]' in installer
    assert '-f "$release_marker"' in installer
    assert '[[ "$(<"$release_marker")" == "$source_revision" ]]' in installer
    assert 'printf \'%s\\n\' "$source_revision" >"$release_marker"' in installer
    assert 'chmod -R a+rX,go-w "$release_dir"' in installer
    assert "release_created=true" in installer
    assert '[[ "$release_created" == true' in installer
    assert 'ln -s -- "$release_dir/venv" "$temporary_link"' in installer
    assert 'mv -Tf -- "$temporary_link" "$active_venv"' in installer
    assert installer.index('[[ -x "$release_executable" && -f "$release_executable"') < (
        installer.index('ln -s -- "$release_dir/venv"')
    )
    assert installer.index('if [[ -e "$active_venv"') < installer.index("install -d")
    assert build_constraints.splitlines() == [
        "hatchling==1.32.0",
        "packaging==26.3",
        "pathspec==1.1.1",
        "pluggy==1.6.0",
        "tomlkit==0.15.1",
        "trove-classifiers==2026.6.1.19",
    ]


def test_systemd_installer_rejects_incompatible_identity_or_python() -> None:
    installer = (ROOT / "deploy" / "systemd" / "install.sh").read_text(encoding="utf-8")

    assert 'python_bin="${PYTHON_BIN:-/usr/bin/python3}"' in installer
    assert 'regular_uid_min="$(awk \'$1 == "UID_MIN"' in installer
    assert 'regular_gid_min="$(awk \'$1 == "GID_MIN"' in installer
    assert 'service_uid" -gt 0 && "$service_uid" -lt "$regular_uid_min"' in installer
    assert 'service_gid" == "$service_group_gid"' in installer
    assert 'service_home" == "$state_dir"' in installer
    assert 'service_shell" == "$nologin_shell"' in installer
    assert 'getent --service=files group "$service_identity"' in installer
    assert 'getent --service=files passwd "$service_identity"' in installer
    assert 'default_group_record="$(getent group "$service_identity")"' in installer
    assert 'default_service_record="$(getent passwd "$service_identity")"' in installer
    assert 'default_group_gid" == "$service_group_gid"' in installer
    assert 'default_service_uid" == "$service_uid"' in installer
    assert 'id -G "$service_identity"' in installer
    assert 'runuser --user "$service_identity" -- "$python_bin"' in installer
    assert "os.access(sys.argv[1], os.X_OK)" in installer
    assert '"$release_executable"' in installer
    assert 'resolved_python="$(readlink -f -- "$python_bin")"' in installer
    assert "/usr/* | /opt/* | /bin/*" in installer
    assert "for the unit sandbox" in installer
    assert 'validate_root_owned_nonwritable_path "$resolved_python"' in installer
    assert "stat -Lc '%u %a'" in installer
    assert 'env -i PATH=/usr/bin:/bin "$release_python" -I -c' in installer
    assert "local_inference_gateway.__file__" in installer
    assert "Path(sys.prefix).resolve()" in installer
    assert 'entrypoint_shebang" == "#!$release_python"' in installer


def test_systemd_installer_builds_only_from_captured_revision() -> None:
    installer = (ROOT / "deploy" / "systemd" / "install.sh").read_text(encoding="utf-8")

    assert 'source_dir="$(mktemp -d)"' in installer
    assert 'git -C "$repo_dir" archive "$source_revision" | tar -x -C "$source_dir"' in installer
    assert '--project "$source_dir"' in installer
    assert '"$source_dir"\n' in installer
    assert '"$source_dir/deploy/systemd/local-inference-gateway.service"' in installer
    assert 'rm -rf -- "$source_dir"' in installer
    assert installer.index('env -i PATH=/usr/bin:/bin "$release_python" -I -c') < (
        installer.rindex("release_created=false")
    )


def test_systemd_installer_guards_managed_paths_and_serializes_installation() -> None:
    installer = (ROOT / "deploy" / "systemd" / "install.sh").read_text(encoding="utf-8")

    assert 'lock_file="/run/local-inference-gateway-install.lock"' in installer
    assert 'exec 9>"$lock_file"' in installer
    assert "flock --exclusive --nonblock 9" in installer
    assert (
        'for managed_path in "$install_root" "$release_root" "$config_dir" "$state_dir"'
        in installer
    )
    assert '[[ ! -L "$managed_path" ]]' in installer
    assert '[[ ! -e "$managed_path" || -d "$managed_path" ]]' in installer
    assert '[[ ! -L "$unit_target" ]]' in installer
    assert 'release_violation="$(' in installer
    assert 'find "$release_dir" -xdev' in installer
    assert "! -user root" in installer
    assert "-perm /022" in installer
    assert 'readlink -f -- "$release_python"' in installer
    assert '! -L "$release_executable"' in installer
    assert '! -L "$release_marker"' in installer
    assert 'find "$release_dir" -xdev -type l -print0' in installer
    assert 'release_link_target="$(readlink -- "$release_link")"' in installer
    assert 'validate_root_owned_nonwritable_path "$release_link_path"' in installer
    assert 'readlink -f -- "$release_link"' in installer
    assert 'validate_root_owned_nonwritable_path "$resolved_release_link"' in installer
    assert installer.index("flock --exclusive --nonblock 9") < installer.index(
        'if ! getent --service=files group "$service_identity"'
    )
    assert installer.index("flock --exclusive --nonblock 9") < installer.index(
        'if [[ -e "$release_dir" ]]'
    )


def test_systemd_installer_neither_provisions_secrets_nor_activates_service() -> None:
    installer = (ROOT / "deploy" / "systemd" / "install.sh").read_text(encoding="utf-8")

    assert "GATEWAY_" not in installer
    assert "/home/juan-canfield" not in installer
    assert "openssl" not in installer
    assert "token_hex" not in installer
    assert "systemctl daemon-reload" in installer
    assert not re.search(
        r"systemctl\s+(?:--[^ ]+\s+)*(?:start|stop|restart|enable|disable)\b",
        installer,
    )
