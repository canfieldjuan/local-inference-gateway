#!/usr/bin/env bash
set -euo pipefail
umask 077
export PATH="/usr/local/bin:/usr/bin:/bin"

unit_name="local-inference-ollama.service"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
unit_source="$repo_dir/deploy/systemd/$unit_name"
ollama_bin="/usr/local/bin/ollama"
model_mount=""
models_dir=""
temporary_environment=""
temporary_unit=""

cleanup() {
  if [[ -n "$temporary_environment" && -f "$temporary_environment" ]]; then
    rm -f -- "$temporary_environment"
  fi
  if [[ -n "$temporary_unit" && -f "$temporary_unit" ]]; then
    rm -f -- "$temporary_unit"
  fi
}
trap cleanup EXIT

fail() {
  echo "error: $*" >&2
  exit 1
}

usage() {
  echo "usage: $0 --model-mount ABSOLUTE_PATH --models-dir ABSOLUTE_PATH" >&2
  exit 2
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
      fail "trusted executable path is not root-owned and non-writable: $current_path"
  done
}

validate_environment_path() {
  local candidate_path="$1"
  local label="$2"

  [[ "$candidate_path" =~ ^/[A-Za-z0-9._/-]+$ ]] ||
    fail "$label must use only absolute path-safe characters"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-mount)
      [[ $# -ge 2 ]] || usage
      model_mount="$2"
      shift 2
      ;;
    --models-dir)
      [[ $# -ge 2 ]] || usage
      models_dir="$2"
      shift 2
      ;;
    *) usage ;;
  esac
done

[[ -n "$model_mount" && -n "$models_dir" ]] || usage
[[ "${EUID:-$(id -u)}" -ne 0 ]] || fail "run this installer as the service user, not root"
for command_name in chmod getent git id install loginctl mktemp mountpoint mv readlink rm stat systemctl; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is required"
done

current_uid="$(id -u)"
current_user="$(id -un)"
passwd_record="$(getent passwd "$current_uid")"
IFS=: read -r passwd_name _ passwd_uid _ _ home_dir _ <<<"$passwd_record"
[[ "$passwd_name" == "$current_user" && "$passwd_uid" == "$current_uid" && "$home_dir" == /* ]] ||
  fail "cannot resolve the current user's home directory"
[[ "$(loginctl show-user "$current_user" -p Linger --value)" == yes ]] ||
  fail "systemd user lingering must be enabled before installation"
systemctl --user show-environment >/dev/null || fail "the systemd user manager is unavailable"

[[ -f "$ollama_bin" && -x "$ollama_bin" && ! -L "$ollama_bin" ]] ||
  fail "$ollama_bin must be a regular executable, not a symbolic link"
resolved_ollama="$(readlink -f -- "$ollama_bin")"
[[ "$resolved_ollama" == "$ollama_bin" ]] || fail "$ollama_bin must resolve to itself"
validate_root_owned_nonwritable_path "$resolved_ollama"

validate_environment_path "$model_mount" "model mount"
validate_environment_path "$models_dir" "models directory"
[[ -d "$model_mount" ]] || fail "model mount does not exist: $model_mount"
[[ -d "$models_dir" ]] || fail "models directory does not exist: $models_dir"
resolved_mount="$(readlink -e -- "$model_mount")"
resolved_models="$(readlink -e -- "$models_dir")"
validate_environment_path "$resolved_mount" "resolved model mount"
validate_environment_path "$resolved_models" "resolved models directory"
mountpoint --quiet "$resolved_mount" || fail "model mount is not an active mount point: $resolved_mount"
case "$resolved_models" in
  "$resolved_mount"/*) ;;
  *) fail "models directory must be below the configured model mount" ;;
esac

git --no-optional-locks -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
  fail "installer must run from a Git checkout"
[[ -z "$(git --no-optional-locks -C "$repo_dir" status --porcelain --untracked-files=all)" ]] ||
  fail "refusing to install a dirty checkout"
[[ -f "$unit_source" && ! -L "$unit_source" ]] || fail "reviewed user unit is missing"

config_dir="$home_dir/.config/local-inference-gateway"
user_unit_dir="$home_dir/.config/systemd/user"
environment_target="$config_dir/ollama-worker.env"
unit_target="$user_unit_dir/$unit_name"
for managed_directory in \
  "$home_dir/.config" "$config_dir" "$home_dir/.config/systemd" "$user_unit_dir"; do
  [[ ! -L "$managed_directory" ]] ||
    fail "managed directory must not be a symbolic link: $managed_directory"
  [[ ! -e "$managed_directory" || -d "$managed_directory" ]] ||
    fail "managed path must be absent or a directory: $managed_directory"
done
for managed_target in "$environment_target" "$unit_target"; do
  [[ ! -L "$managed_target" ]] || fail "managed target must not be a symbolic link: $managed_target"
  [[ ! -e "$managed_target" || -f "$managed_target" ]] ||
    fail "managed target must be absent or a regular file: $managed_target"
done

install -d -m 0700 "$config_dir"
install -d -m 0755 "$user_unit_dir"
temporary_environment="$(mktemp "$config_dir/.ollama-worker.env.XXXXXX")"
temporary_unit="$(mktemp "$user_unit_dir/.${unit_name}.XXXXXX")"
{
  printf 'OLLAMA_MODEL_MOUNT=%s\n' "$resolved_mount"
  printf 'OLLAMA_MODELS=%s\n' "$resolved_models"
  printf '%s\n' \
    'OLLAMA_HOST=127.0.0.1:11434' \
    'OLLAMA_NO_CLOUD=1' \
    'OLLAMA_CONTEXT_LENGTH=8192' \
    'OLLAMA_MAX_LOADED_MODELS=1' \
    'OLLAMA_NUM_PARALLEL=1'
} >"$temporary_environment"
chmod 0600 "$temporary_environment"
install -m 0644 "$unit_source" "$temporary_unit"
mv -f -- "$temporary_environment" "$environment_target"
temporary_environment=""
mv -f -- "$temporary_unit" "$unit_target"
temporary_unit=""
systemctl --user daemon-reload

echo "Installed $unit_name for $current_user."
echo "The service was not enabled or started."
