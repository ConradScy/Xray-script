#!/usr/bin/env bash
# Read-only platform/dependency checks; no package or account mutations.
function xray_install_platform() {
    local file="${1:-/etc/os-release}" id version
    [[ -r "${file}" ]] || return 1
    id="$(sed -n 's/^ID=//p' "${file}" | tr -d '\"\047')"
    version="$(sed -n 's/^VERSION_ID=//p' "${file}" | tr -d '\"\047')"
    printf '%s:%s\n' "${id}" "${version%%.*}"
}

function xray_service_property() {
    local name="$1" output
    output="$(systemctl show -p "${name}" xray --no-pager --full 2>/dev/null)" || return 1
    # systemd 219 prints no line for an empty array or an unknown optional property.
    [[ -n "${output}" || "${2:-}" != optional ]] || return 0
    [[ "${output}" == "${name}="* && "${output}" != *$'\n'* ]] || return 1
    printf '%s' "${output#*=}"
}

# Some CentOS 7 builds fail an unknown-property-only query. Anchor this optional
# newer property to LoadState instead of suppressing a failed read or guessing
# support from the systemd version. Do not collect/log unrelated properties.
function xray_optional_root_image() {
    local output line load_count=0 image_count=0 root_image=''
    output="$(systemctl show -p LoadState -p RootImage xray --no-pager --full 2>/dev/null)" || {
        echo '[CentOS 7 preflight] RootImage.query-failed' >&2
        return 1
    }
    while IFS= read -r line; do
        case "${line}" in
        LoadState=loaded) load_count=$((load_count + 1)) ;;
        RootImage=*) image_count=$((image_count + 1)); root_image="${line#RootImage=}" ;;
        *) echo '[CentOS 7 preflight] RootImage.invalid-response' >&2; return 1 ;;
        esac
    done <<<"${output}"
    if [[ "${load_count}" != 1 || "${image_count}" -gt 1 ]]; then
        echo '[CentOS 7 preflight] RootImage.invalid-response' >&2
        return 1
    fi
    printf '%s' "${root_image}"
}

function xray_sysusers_candidates() {
    type -P systemd-sysusers 2>/dev/null || true
    printf '%s\n' /usr/lib/systemd/systemd-sysusers /lib/systemd/systemd-sysusers
}

function xray_sysusers_preflight() {
    local tool
    while IFS= read -r tool; do
        if [[ -f "${tool}" && -x "${tool}" ]] && "${tool}" --version >/dev/null 2>&1; then
            PATH="${tool%/*}:${PATH}"
            export PATH
            return 0
        fi
    done < <(xray_sysusers_candidates)
    echo '缺少可用的 systemd-sysusers；未调用官方安装器，也未停止 Xray。 / A working systemd-sysusers is required; Xray was not stopped.' >&2
    echo '请检查 /etc/os-release 并从该发行版官方仓库恢复对应 systemd 工具。CentOS 7 不提供此工具，请使用本脚本的“仅安装/更新”已有核心兼容路径；不要伪造命令或盲目重装 systemd。' >&2
    return 1
}

function xray_reject_centos7_reconfigure() {
    if [[ "$(xray_install_platform)" == centos:7 ]]; then
        echo 'CentOS 7 仅支持已有标准 Xray 服务的核心升级（主菜单 2）；不支持新装、快速安装或整套配置重建。请迁移到受支持的发行版后新装。 / CentOS 7: existing-core upgrades only; fresh/full installation is unsupported.' >&2
        return 1
    fi
}
