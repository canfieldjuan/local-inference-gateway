#!/usr/bin/env bash
set -euo pipefail

service_identity="local-inference-gateway"
install_root="/opt/local-inference-gateway"
release_root="$install_root/releases"
active_venv="$install_root/venv"
config_dir="/etc/local-inference-gateway"
state_dir="/var/lib/local-inference-gateway"
unit_target="/etc/systemd/system/local-inference-gateway.service"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
uv_bin="${UV_BIN:-$(command -v uv || true)}"
constraints_file=""
temporary_link=""
release_created=false

cleanup() {
  if [[ -n "$constraints_file" ]]; then
    rm -f -- "$constraints_file"
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

[[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "run this installer as root"
for command_name in chmod git getent groupadd install ln mktemp mv rm systemctl useradd; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is required"
done
[[ "$uv_bin" == /* && -f "$uv_bin" && -x "$uv_bin" ]] ||
  fail "UV_BIN must name an absolute executable uv path"
git -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
  fail "installer must run from a Git checkout"
[[ -z "$(git -C "$repo_dir" status --porcelain --untracked-files=all)" ]] ||
  fail "refusing to install a dirty checkout"

source_revision="$(git -C "$repo_dir" rev-parse --verify 'HEAD^{commit}')"
[[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || fail "source revision is invalid"
release_dir="$release_root/$source_revision"
release_executable="$release_dir/venv/bin/local-inference-gateway"
release_marker="$release_dir/SOURCE_REVISION"

if [[ -e "$active_venv" && ! -L "$active_venv" ]]; then
  fail "$active_venv must be absent or a symbolic link"
fi

if ! getent group "$service_identity" >/dev/null; then
  groupadd --system "$service_identity"
fi
if ! getent passwd "$service_identity" >/dev/null; then
  useradd \
    --system \
    --gid "$service_identity" \
    --home-dir "$state_dir" \
    --shell /usr/sbin/nologin \
    "$service_identity"
fi

install -d -o root -g root -m 0755 "$install_root" "$release_root"
install -d -o root -g "$service_identity" -m 0750 "$config_dir"
install -d -o "$service_identity" -g "$service_identity" -m 0700 "$state_dir"

[[ ! -L "$release_dir" ]] || fail "release path must not be a symbolic link: $release_dir"
if [[ -e "$release_dir" ]]; then
  [[ -d "$release_dir" && -x "$release_executable" && -f "$release_marker" ]] ||
    fail "existing release is incomplete: $release_dir"
  [[ "$(<"$release_marker")" == "$source_revision" ]] ||
    fail "existing release identity does not match its path: $release_dir"
else
  constraints_file="$(mktemp)"
  install -d -o root -g root -m 0755 "$release_dir"
  release_created=true
  "$uv_bin" export \
    --project "$repo_dir" \
    --locked \
    --no-dev \
    --no-emit-project \
    --format requirements.txt \
    --output-file "$constraints_file" >/dev/null
  "$uv_bin" venv "$release_dir/venv"
  "$uv_bin" pip install \
    --python "$release_dir/venv/bin/python" \
    --constraints "$constraints_file" \
    "$repo_dir"
  [[ -x "$release_executable" ]] ||
    fail "installed release has no gateway executable"
  printf '%s\n' "$source_revision" >"$release_marker"
  chmod -R go-w "$release_dir"
  release_created=false
fi

temporary_link="$install_root/.venv-link.$$"
ln -s -- "$release_dir/venv" "$temporary_link"
mv -Tf -- "$temporary_link" "$active_venv"
temporary_link=""

install -o root -g root -m 0644 \
  "$repo_dir/deploy/systemd/local-inference-gateway.service" "$unit_target"
systemctl daemon-reload

echo "Installed Local Inference Gateway revision $source_revision."
echo "The service was not enabled or started."
echo "Provision the private files under $config_dir, validate them, then activate explicitly."
