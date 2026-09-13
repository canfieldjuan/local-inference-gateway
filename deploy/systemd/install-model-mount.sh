#!/usr/bin/env bash
set -euo pipefail
umask 077
export PATH="/usr/sbin:/usr/bin:/sbin:/bin"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
unit_dir="/etc/systemd/system"
managed_marker="# Managed by local-inference-gateway install-model-mount.sh"
filesystem_uuid=""
mount_point=""
models_dir=""
owner_user=""
temporary_unit=""
validation_dir=""

cleanup() {
  if [[ -n "$temporary_unit" && -f "$temporary_unit" ]]; then
    rm -f -- "$temporary_unit"
  fi
  if [[ -n "$validation_dir" && -d "$validation_dir" ]]; then
    rm -rf -- "$validation_dir"
  fi
}
trap cleanup EXIT

fail() {
  echo "error: $*" >&2
  exit 1
}

usage() {
  echo "usage: $0 --uuid UUID --mount-point ABSOLUTE_PATH --models-dir ABSOLUTE_PATH --owner-user USER" >&2
  exit 2
}

validate_path() {
  local candidate_path="$1"
  local label="$2"

  [[ "$candidate_path" != / ]] || fail "$label cannot be the root filesystem"
  [[ "$candidate_path" =~ ^/[A-Za-z0-9._/-]+$ ]] ||
    fail "$label must use only absolute path-safe characters"
}

require_mount_option() {
  local expected_option="$1"

  case ",$mounted_options," in
    *",$expected_option,"*) ;;
    *) fail "current mount is missing required option: $expected_option" ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uuid)
      [[ $# -ge 2 ]] || usage
      filesystem_uuid="$2"
      shift 2
      ;;
    --mount-point)
      [[ $# -ge 2 ]] || usage
      mount_point="$2"
      shift 2
      ;;
    --models-dir)
      [[ $# -ge 2 ]] || usage
      models_dir="$2"
      shift 2
      ;;
    --owner-user)
      [[ $# -ge 2 ]] || usage
      owner_user="$2"
      shift 2
      ;;
    *) usage ;;
  esac
done

[[ -n "$filesystem_uuid" && -n "$mount_point" && -n "$models_dir" && -n "$owner_user" ]] ||
  usage
[[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "run this installer as root"
for command_name in \
  blkid findmnt getent git grep id install mktemp mv readlink rm sed stat systemctl \
  systemd-analyze systemd-escape; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is required"
done

[[ "$filesystem_uuid" =~ ^[A-Fa-f0-9-]{4,64}$ ]] || fail "filesystem UUID is invalid"
[[ "$owner_user" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || fail "owner user is invalid"
validate_path "$mount_point" "mount point"
validate_path "$models_dir" "models directory"
[[ -d "$mount_point" && ! -L "$mount_point" ]] ||
  fail "mount point must be an existing non-symlink directory"
[[ -d "$models_dir" && ! -L "$models_dir" ]] ||
  fail "models directory must be an existing non-symlink directory"
resolved_mount="$(readlink -e -- "$mount_point")" || fail "cannot resolve mount point"
resolved_models="$(readlink -e -- "$models_dir")" || fail "cannot resolve models directory"
[[ "$resolved_mount" == "$mount_point" ]] || fail "mount point must already be canonical"
[[ "$resolved_models" == "$models_dir" ]] || fail "models directory must already be canonical"
case "$resolved_models" in
  "$resolved_mount"/*) ;;
  *) fail "models directory must be below the mount point" ;;
esac

owner_record="$(getent --service=files passwd "$owner_user")" ||
  fail "owner user must be a local account"
IFS=: read -r resolved_owner _ owner_uid owner_gid _ owner_home _ <<<"$owner_record"
[[ "$resolved_owner" == "$owner_user" && "$owner_uid" =~ ^[0-9]+$ && "$owner_gid" =~ ^[0-9]+$ ]] ||
  fail "owner user record is invalid"
[[ "$owner_uid" -gt 0 && "$owner_gid" -gt 0 && "$owner_home" == /* ]] ||
  fail "owner user must have non-root numeric identity and an absolute home"
default_owner_record="$(getent passwd "$owner_user")" || fail "owner user is unavailable"
IFS=: read -r default_owner _ default_uid default_gid _ _ _ <<<"$default_owner_record"
[[ "$default_owner" == "$owner_user" && "$default_uid" == "$owner_uid" && \
  "$default_gid" == "$owner_gid" ]] || fail "default NSS does not select the local owner user"

device_path="/dev/disk/by-uuid/$filesystem_uuid"
[[ -L "$device_path" ]] || fail "filesystem UUID does not name an existing device"
resolved_device="$(readlink -f -- "$device_path")" || fail "cannot resolve filesystem device"
[[ -b "$resolved_device" ]] || fail "filesystem UUID does not resolve to a block device"
[[ "$(blkid -s UUID -o value "$resolved_device")" == "$filesystem_uuid" ]] ||
  fail "filesystem UUID does not match the resolved device"

mount_record="$(findmnt --mountpoint "$resolved_mount" --noheadings --output SOURCE,FSTYPE,OPTIONS)" ||
  fail "mount point is not currently mounted"
read -r mounted_source mounted_type mounted_options <<<"$mount_record"
resolved_mounted_source="$(readlink -f -- "$mounted_source")" ||
  fail "cannot resolve the current mount source"
[[ "$resolved_mounted_source" == "$resolved_device" ]] ||
  fail "current mount source does not match the filesystem UUID"
[[ "$mounted_type" == ntfs3 ]] || fail "current filesystem must use ntfs3"
for required_option in rw nosuid nodev "uid=$owner_uid" "gid=$owner_gid" acl iocharset=utf8 prealloc; do
  require_mount_option "$required_option"
done

findmnt --verify --tab-file /etc/fstab >/dev/null || fail "/etc/fstab is invalid"
if findmnt --fstab --evaluate --target "$resolved_mount" >/dev/null 2>&1; then
  fail "/etc/fstab already configures the mount point"
fi
configured_sources="$(findmnt --fstab --evaluate --noheadings --output SOURCE)" ||
  fail "cannot inspect configured /etc/fstab sources"
while IFS= read -r configured_source; do
  [[ -n "$configured_source" ]] || continue
  configured_device="$(readlink -f -- "$configured_source")" ||
    fail "cannot resolve configured /etc/fstab source"
  [[ "$configured_device" != "$resolved_device" ]] ||
    fail "/etc/fstab already configures the filesystem device"
done <<<"$configured_sources"

git --no-optional-locks -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
  fail "installer must run from a Git checkout"
checkout_status="$(git --no-optional-locks -C "$repo_dir" status --porcelain --untracked-files=all)" ||
  fail "cannot determine checkout cleanliness"
[[ -z "$checkout_status" ]] || fail "refusing to install a dirty checkout"
source_revision="$(git --no-optional-locks -C "$repo_dir" rev-parse --verify 'HEAD^{commit}')" ||
  fail "cannot determine source revision"
[[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || fail "source revision is invalid"
template_blob="$source_revision:deploy/systemd/local-inference-model-storage.mount.in"
[[ "$(git --no-optional-locks -C "$repo_dir" cat-file -t "$template_blob")" == blob ]] ||
  fail "reviewed mount-unit template is missing"

unit_name="$(systemd-escape --path --suffix=mount "$resolved_mount")" ||
  fail "cannot derive mount unit name"
[[ "$unit_name" == *.mount && "$unit_name" != */* ]] || fail "derived mount unit name is invalid"
unit_target="$unit_dir/$unit_name"
[[ ! -L "$unit_target" ]] || fail "managed mount unit must not be a symbolic link"
[[ ! -e "$unit_target" || -f "$unit_target" ]] ||
  fail "managed mount unit must be absent or a regular file"
if [[ -f "$unit_target" ]]; then
  IFS= read -r existing_marker <"$unit_target" || fail "cannot read existing mount unit"
  [[ "$existing_marker" == "$managed_marker" ]] ||
    fail "refusing to replace an unmanaged mount unit"
fi
[[ "$(stat -Lc '%u %a' -- "$unit_dir")" == "0 755" ]] ||
  fail "$unit_dir must be root-owned with mode 0755"

validation_dir="$(mktemp -d)"
validation_unit="$validation_dir/$unit_name"
git --no-optional-locks -C "$repo_dir" cat-file blob "$template_blob" |
  sed \
    -e "s|@DEVICE@|$device_path|g" \
    -e "s|@MOUNT_POINT@|$resolved_mount|g" \
    -e "s|@OWNER_UID@|$owner_uid|g" \
    -e "s|@OWNER_GID@|$owner_gid|g" >"$validation_unit"
if grep -q '@[A-Z_][A-Z_]*@' "$validation_unit"; then
  fail "rendered mount unit contains an unresolved placeholder"
fi
systemd-analyze verify "$validation_unit"
temporary_unit="$(mktemp "$unit_dir/.${unit_name}.XXXXXX")"
install -o root -g root -m 0644 "$validation_unit" "$temporary_unit"
mv -f -- "$temporary_unit" "$unit_target"
temporary_unit=""
rm -rf -- "$validation_dir"
validation_dir=""
systemctl daemon-reload

echo "Installed $unit_name from revision $source_revision."
echo "The mount unit was not enabled or started."
