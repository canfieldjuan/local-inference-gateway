#!/usr/bin/env bash
set -euo pipefail
umask 077
export PATH="/usr/sbin:/usr/bin:/sbin:/bin"

service_identity="local-inference-gateway"
install_root="/opt/local-inference-gateway"
release_root="$install_root/releases"
active_venv="$install_root/venv"
config_dir="/etc/local-inference-gateway"
state_dir="/var/lib/local-inference-gateway"
unit_target="/etc/systemd/system/local-inference-gateway.service"
lock_file="/run/local-inference-gateway-install.lock"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
uv_bin="${UV_BIN:-$(command -v uv || true)}"
python_bin="${PYTHON_BIN:-/usr/bin/python3}"
nologin_shell="/usr/sbin/nologin"
build_constraints_file=""
constraints_file=""
source_dir=""
temporary_link=""
release_created=false

cleanup() {
  if [[ -n "$constraints_file" ]]; then
    rm -f -- "$constraints_file"
  fi
  if [[ -n "$source_dir" && -d "$source_dir" ]]; then
    rm -rf -- "$source_dir"
  fi
  if [[ "$release_created" == true && -n "${release_dir:-}" && -d "$release_dir" ]]; then
    rm -rf -- "$release_dir"
  fi
  if [[ -n "$temporary_link" && -L "$temporary_link" ]]; then
    rm -f -- "$temporary_link"
  fi
}
trap cleanup EXIT

fail() {
  echo "error: $*" >&2
  exit 1
}

uv_command() {
  env -i HOME=/root PATH="$PATH" "$uv_bin" --no-config "$@"
}

validate_root_owned_nonwritable_path() {
  local candidate_path="$1"
  local current_path=""
  local metadata path_mode path_owner path_part
  local -a path_parts checked_paths=("/")

  IFS=/ read -r -a path_parts <<<"${candidate_path#/}"
  for path_part in "${path_parts[@]}"; do
    [[ -n "$path_part" ]] || continue
    current_path="$current_path/$path_part"
    checked_paths+=("$current_path")
  done
  for current_path in "${checked_paths[@]}"; do
    metadata="$(stat -Lc '%u %a' -- "$current_path")" ||
      fail "cannot inspect trusted path component: $current_path"
    read -r path_owner path_mode <<<"$metadata"
    [[ "$path_owner" == 0 && "$((8#$path_mode & 0022))" -eq 0 ]] ||
      fail "trusted path component is not root-owned and non-writable: $current_path"
  done
}

validate_unit_visible_path() {
  local candidate_path="$1"

  case "$candidate_path" in
    /home | /home/* | /root | /root/* | /run/user | /run/user/*)
      fail "selected Python runtime path is hidden by the unit sandbox: $candidate_path"
      ;;
  esac
}

validate_release_symlinks() {
  local release_link release_link_path release_link_target resolved_release_link

  while IFS= read -r -d '' release_link; do
    release_link_target="$(readlink -- "$release_link")" ||
      fail "release contains an unreadable symbolic link: $release_link"
    case "$release_link_target" in
      /*) release_link_path="$release_link_target" ;;
      *) release_link_path="${release_link%/*}/$release_link_target" ;;
    esac
    validate_root_owned_nonwritable_path "$release_link_path"
    resolved_release_link="$(readlink -f -- "$release_link")" ||
      fail "release contains an unresolved symbolic link: $release_link"
    [[ -e "$resolved_release_link" ]] ||
      fail "release contains a dangling symbolic link: $release_link"
    case "$resolved_release_link" in
      "$release_dir"/*) ;;
      *)
        [[ -f "$resolved_release_link" ]] ||
          fail "release symbolic link targets an external non-file: $release_link"
        validate_root_owned_nonwritable_path "$resolved_release_link"
        ;;
    esac
  done < <(find "$release_dir" -xdev -type l -print0)
}

[[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "run this installer as root"
for command_name in \
  awk chmod env find flock git getent groupadd id install ln mktemp mv readlink rm runuser setpriv stat \
  systemctl tar useradd; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is required"
done
[[ "$uv_bin" == /* && -f "$uv_bin" && -x "$uv_bin" ]] ||
  fail "UV_BIN must name an absolute executable uv path"
resolved_uv="$(readlink -f -- "$uv_bin")"
[[ "$resolved_uv" == /* && -f "$resolved_uv" && -x "$resolved_uv" ]] ||
  fail "UV_BIN does not resolve to an executable uv path"
validate_root_owned_nonwritable_path "$resolved_uv"
uv_bin="$resolved_uv"
[[ "$python_bin" == /* && -f "$python_bin" && -x "$python_bin" ]] ||
  fail "PYTHON_BIN must name an absolute executable Python path"
resolved_python="$(readlink -f -- "$python_bin")"
[[ "$resolved_python" == /* && -f "$resolved_python" && -x "$resolved_python" ]] ||
  fail "PYTHON_BIN does not resolve to an executable Python path"
case "$resolved_python" in
  /usr/* | /opt/* | /bin/*) ;;
  *) fail "PYTHON_BIN must resolve under /usr, /opt, or /bin for the unit sandbox" ;;
esac
validate_root_owned_nonwritable_path "$resolved_python"
python_bin="$resolved_python"
[[ -x "$nologin_shell" ]] || fail "$nologin_shell is required"
[[ -f /etc/login.defs ]] || fail "/etc/login.defs is required"
regular_uid_min="$(awk '$1 == "UID_MIN" {print $2; exit}' /etc/login.defs)"
regular_gid_min="$(awk '$1 == "GID_MIN" {print $2; exit}' /etc/login.defs)"
[[ "$regular_uid_min" =~ ^[0-9]+$ && "$regular_uid_min" -gt 0 ]] ||
  fail "UID_MIN is invalid"
[[ "$regular_gid_min" =~ ^[0-9]+$ && "$regular_gid_min" -gt 0 ]] ||
  fail "GID_MIN is invalid"
git -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
  fail "installer must run from a Git checkout"
[[ -z "$(git -C "$repo_dir" status --porcelain --untracked-files=all)" ]] ||
  fail "refusing to install a dirty checkout"

source_revision="$(git -C "$repo_dir" rev-parse --verify 'HEAD^{commit}')"
[[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || fail "source revision is invalid"
source_dir="$(mktemp -d)"
git -C "$repo_dir" archive "$source_revision" | tar -x -C "$source_dir"
build_constraints_file="$source_dir/deploy/systemd/build-constraints.txt"
[[ -f "$build_constraints_file" ]] || fail "build constraints are missing"
exec 9>"$lock_file"
flock --exclusive --nonblock 9 || fail "another gateway installation is already running"
release_dir="$release_root/$source_revision"
release_executable="$release_dir/venv/bin/local-inference-gateway"
release_python="$release_dir/venv/bin/python"
release_marker="$release_dir/SOURCE_REVISION"

if [[ -e "$active_venv" && ! -L "$active_venv" ]]; then
  fail "$active_venv must be absent or a symbolic link"
fi
for managed_path in "$install_root" "$release_root" "$config_dir" "$state_dir"; do
  [[ ! -L "$managed_path" ]] || fail "managed directory must not be a symbolic link: $managed_path"
  [[ ! -e "$managed_path" || -d "$managed_path" ]] ||
    fail "managed path must be absent or a directory: $managed_path"
done
[[ ! -L "$unit_target" ]] || fail "unit target must not be a symbolic link: $unit_target"
[[ ! -e "$unit_target" || -f "$unit_target" ]] ||
  fail "unit target must be absent or a regular file: $unit_target"

if ! getent --service=files group "$service_identity" >/dev/null; then
  ! getent group "$service_identity" >/dev/null ||
    fail "service group exists outside the local files database"
  groupadd --system "$service_identity"
fi
if ! getent --service=files passwd "$service_identity" >/dev/null; then
  ! getent passwd "$service_identity" >/dev/null ||
    fail "service account exists outside the local files database"
  useradd \
    --system \
    --gid "$service_identity" \
    --home-dir "$state_dir" \
    --shell "$nologin_shell" \
    "$service_identity"
fi

service_group_record="$(getent --service=files group "$service_identity")"
IFS=: read -r service_group_name _ service_group_gid _ <<<"$service_group_record"
[[ "$service_group_name" == "$service_identity" && "$service_group_gid" =~ ^[0-9]+$ ]] ||
  fail "existing service group is incompatible"
[[ "$service_group_gid" -gt 0 && "$service_group_gid" -lt "$regular_gid_min" ]] ||
  fail "existing service group is not a system group"

service_record="$(getent --service=files passwd "$service_identity")"
IFS=: read -r service_name _ service_uid service_gid _ service_home service_shell \
  <<<"$service_record"
[[ "$service_name" == "$service_identity" && "$service_uid" =~ ^[0-9]+$ ]] ||
  fail "existing service account is incompatible"
[[ "$service_uid" -gt 0 && "$service_uid" -lt "$regular_uid_min" ]] ||
  fail "existing service account is not a system account"
[[ "$service_gid" == "$service_group_gid" ]] || fail "service account primary group is incompatible"
[[ "$service_home" == "$state_dir" ]] || fail "service account home is incompatible"
[[ "$service_shell" == "$nologin_shell" ]] || fail "service account shell is incompatible"
passwd_alias="$(getent --service=files passwd | awk -F: \
  -v expected_name="$service_identity" -v expected_id="$service_uid" \
  '$3 == expected_id && $1 != expected_name { print $1; exit }')"
[[ -z "$passwd_alias" ]] || fail "service UID is also assigned to local account: $passwd_alias"
group_alias="$(getent --service=files group | awk -F: \
  -v expected_name="$service_identity" -v expected_id="$service_group_gid" \
  '$3 == expected_id && $1 != expected_name { print $1; exit }')"
[[ -z "$group_alias" ]] || fail "service GID is also assigned to local group: $group_alias"

default_group_record="$(getent group "$service_identity")"
IFS=: read -r default_group_name _ default_group_gid _ <<<"$default_group_record"
[[ "$default_group_name" == "$service_group_name" && \
  "$default_group_gid" == "$service_group_gid" ]] ||
  fail "default NSS does not select the local service group"
default_service_record="$(getent passwd "$service_identity")"
IFS=: read -r default_service_name _ default_service_uid default_service_gid _ \
  default_service_home default_service_shell <<<"$default_service_record"
[[ "$default_service_name" == "$service_name" && "$default_service_uid" == "$service_uid" && \
  "$default_service_gid" == "$service_gid" && "$default_service_home" == "$service_home" && \
  "$default_service_shell" == "$service_shell" ]] ||
  fail "default NSS does not select the local service account"
[[ "$(id -G "$service_identity")" == "$service_group_gid" ]] ||
  fail "service account has supplementary group memberships"
python_runtime_paths="$(
  setpriv --reuid 65534 --regid 65534 --clear-groups \
    --inh-caps=-all --ambient-caps=-all --bounding-set=-all --no-new-privs \
    env -i HOME=/nonexistent PATH=/usr/bin:/bin "$python_bin" -I -S -c \
    'import sys

raise SystemExit(2) if sys.version_info < (3, 12) else print("\n".join(sys.path))'
)" || fail "cannot safely inspect the selected Python 3.12+ runtime"
[[ -n "$python_runtime_paths" ]] || fail "selected Python runtime has no import paths"
while IFS= read -r python_runtime_path; do
  [[ "$python_runtime_path" == /* ]] || fail "selected Python runtime path is not absolute"
  validate_unit_visible_path "$python_runtime_path"
  if [[ -e "$python_runtime_path" ]]; then
    validate_root_owned_nonwritable_path "$python_runtime_path"
    trusted_runtime_path="$(readlink -f -- "$python_runtime_path")" ||
      fail "cannot resolve selected Python runtime path: $python_runtime_path"
  else
    python_runtime_parent="${python_runtime_path%/*}"
    validate_root_owned_nonwritable_path "$python_runtime_parent"
    trusted_runtime_path="$(readlink -f -- "$python_runtime_parent")" ||
      fail "cannot resolve selected Python runtime parent: $python_runtime_path"
  fi
  validate_unit_visible_path "$trusted_runtime_path"
  validate_root_owned_nonwritable_path "$trusted_runtime_path"
done <<<"$python_runtime_paths"
runuser --user "$service_identity" -- \
  env -i HOME="$service_home" PATH=/usr/bin:/bin "$python_bin" -I -S -c \
  'import sys; raise SystemExit(sys.version_info < (3, 12))' >/dev/null 2>&1 ||
  fail "service account cannot execute the selected Python 3.12+ interpreter"

install -d -o root -g root -m 0755 "$install_root" "$release_root"
install -d -o root -g "$service_identity" -m 0750 "$config_dir"
install -d -o "$service_identity" -g "$service_identity" -m 0700 "$state_dir"

[[ ! -L "$release_dir" ]] || fail "release path must not be a symbolic link: $release_dir"
if [[ -e "$release_dir" ]]; then
  [[ -d "$release_dir" && -x "$release_executable" && -f "$release_executable" && \
    ! -L "$release_executable" && -f "$release_marker" && ! -L "$release_marker" ]] ||
    fail "existing release is incomplete: $release_dir"
  [[ "$(<"$release_marker")" == "$source_revision" ]] ||
    fail "existing release identity does not match its path: $release_dir"
else
  constraints_file="$(mktemp)"
  install -d -o root -g root -m 0755 "$release_dir"
  release_created=true
  uv_command export \
    --project "$source_dir" \
    --locked \
    --no-dev \
    --no-emit-project \
    --format requirements.txt \
    --output-file "$constraints_file" >/dev/null
  uv_command venv --python "$python_bin" "$release_dir/venv"
  uv_command pip install \
    --python "$release_dir/venv/bin/python" \
    --link-mode copy \
    --constraints "$constraints_file" \
    --build-constraints "$build_constraints_file" \
    "$source_dir"
  [[ -x "$release_executable" && -f "$release_executable" && ! -L "$release_executable" ]] ||
    fail "installed release has no gateway executable"
  printf '%s\n' "$source_revision" >"$release_marker"
  chmod -R a+rX,go-w "$release_dir"
fi

release_violation="$(
  find "$release_dir" -xdev \
    \( ! -user root -o \( \( -type f -o -type d \) -perm /022 \) \) -print -quit
)"
[[ -z "$release_violation" ]] || fail "release is not immutable: $release_violation"
validate_release_symlinks
entrypoint_shebang=""
IFS= read -r entrypoint_shebang <"$release_executable" ||
  fail "installed gateway entrypoint has no interpreter declaration"
[[ "$entrypoint_shebang" == "#!$release_python" ]] ||
  fail "installed gateway entrypoint does not use the release interpreter"
runuser --user "$service_identity" -- \
  env -i PATH=/usr/bin:/bin "$python_bin" -I -c \
  'import os
import sys

raise SystemExit(not os.access(sys.argv[1], os.X_OK))' \
  "$release_executable" >/dev/null 2>&1 ||
  fail "service account cannot execute the installed gateway entrypoint"
runuser --user "$service_identity" -- \
  env -i PATH=/usr/bin:/bin "$release_python" -I -c \
  'import runpy
import sys
import types

called = []

def main():
    called.append(True)

target = types.ModuleType("local_inference_gateway.__main__")
target.main = main
sys.modules[target.__name__] = target
try:
    runpy.run_path(sys.argv[1], run_name="__main__")
except SystemExit as error:
    if error.code not in (None, 0):
        raise
raise SystemExit(not called)' \
  "$release_executable" >/dev/null 2>&1 ||
  fail "installed gateway entrypoint does not invoke its declared console target"
resolved_release_python="$(readlink -f -- "$release_python")"
if [[ "$resolved_release_python" != "$python_bin" && \
  "$resolved_release_python" != "$release_dir"/* ]]; then
  fail "installed Python resolves outside the selected interpreter and release"
fi
runuser --user "$service_identity" -- \
  env -i PATH=/usr/bin:/bin "$release_python" -I -c \
  'from pathlib import Path
import local_inference_gateway
import local_inference_gateway.__main__ as gateway_main
import sys

module_path = Path(local_inference_gateway.__file__).resolve()
main_path = Path(gateway_main.__file__).resolve()
release_prefix = Path(sys.prefix).resolve()
raise SystemExit(
    not module_path.is_relative_to(release_prefix)
    or not main_path.is_relative_to(release_prefix)
    or not callable(gateway_main.main)
)' \
  >/dev/null 2>&1 ||
  fail "service account cannot execute the installed gateway release"
release_created=false

temporary_link="$install_root/.venv-link.$$"
ln -s -- "$release_dir/venv" "$temporary_link"
mv -Tf -- "$temporary_link" "$active_venv"
temporary_link=""

install -o root -g root -m 0644 \
  "$source_dir/deploy/systemd/local-inference-gateway.service" "$unit_target"
systemctl daemon-reload

echo "Installed Local Inference Gateway revision $source_revision."
echo "The service was not enabled or started."
echo "Provision the private files under $config_dir, validate them, then activate explicitly."
