#!/usr/bin/env bash
# CentOS 7 existing-service core upgrades only. Never provisions accounts/units.
function legacy_binary_path() { printf '%s\n' /usr/local/bin/xray; }
function legacy_config_path() { printf '%s\n' "${XRAY_CONFIG_PATH:-/usr/local/etc/xray/config.json}"; }

function legacy_error() {
    printf '[CentOS 7] %s\n' "$*" >&2
    exit 1
}

function legacy_capabilities() {
    local output
    output="$(getcap "$1" 2>/dev/null)" || return 1
    [[ -z "${output}" ]] || printf '%s' "${output#* }"
}

function legacy_context() {
    if [[ -e /sys/fs/selinux/enforce || -e /selinux/enforce ]]; then
        local context
        context="$(stat -c %C "$1" 2>/dev/null)" || return 1
        [[ -n "${context}" && "${context}" != '?' ]] || return 1
        printf '%s' "${context}"
    fi
}

# Restore metadata only after writing data (writing clears file capabilities).
function legacy_apply_attributes() {
    local reference="$1" target="$2" mode="$3" caps="$4" context="$5" found_caps found_context
    cp --attributes-only --preserve=all -- "${reference}" "${target}" && chmod "${mode}" "${target}" || return 1
    [[ -z "${context}" ]] || chcon --reference="${reference}" "${target}" || return 1
    found_caps="$(legacy_capabilities "${target}")" || return 1
    found_context="$(legacy_context "${target}")" || return 1
    [[ "${found_caps}" == "${caps}" && "${found_context}" == "${context}" &&
        "$(stat -c '%u:%g' "${target}")" == "$(stat -c '%u:%g' "${reference}")" ]]
}

function legacy_binary_replace() {
    local source="$1" destination="$2" reference="$3" mode="$4" caps="$5" context="$6" staged
    staged="$(mktemp "${destination}.upgrade.XXXXXX")" || return 1
    if ! cp -- "${source}" "${staged}" || ! legacy_apply_attributes "${reference}" "${staged}" "${mode}" "${caps}" "${context}"; then
        rm -f -- "${staged}"
        return 1
    fi
    mv -f -- "${staged}" "${destination}" || { rm -f -- "${staged}"; return 1; }
}

function legacy_download_core() {
    local version="$1" directory="$2" candidate="$3" arch archive digest expected actual names listing name
    case "$(uname -m)" in x86_64 | amd64) arch=64 ;; *) echo 'CentOS 7 compatibility currently supports x86_64 only.' >&2; return 1 ;; esac
    archive="${directory}/Xray-linux-${arch}.zip"
    digest="${archive}.dgst"
    curl -fsSL --retry 3 --connect-timeout 15 --max-time 300 "https://github.com/XTLS/Xray-core/releases/download/${version}/Xray-linux-${arch}.zip" -o "${archive}" || return 1
    curl -fsSL --retry 3 --connect-timeout 15 --max-time 60 "https://github.com/XTLS/Xray-core/releases/download/${version}/Xray-linux-${arch}.zip.dgst" -o "${digest}" || return 1
    expected="$(awk -F= '/^[[:space:]]*SHA(2-)?256[[:space:]]*=/ {if (NF != 2) {print "invalid"; next}; gsub(/[[:space:]]/, "", $2); print tolower($2)}' "${digest}")"
    [[ "${expected}" =~ ^[0-9a-f]{64}$ ]] || return 1
    actual="$(sha256sum "${archive}")" || return 1
    [[ "${actual%% *}" == "${expected}" ]] || return 1
    # Extract just one known regular-file member, never archive paths or symlinks.
    names="$(unzip -Z1 "${archive}")" || return 1
    [[ "$(grep -Fxc xray <<<"${names}")" == 1 ]] || return 1
    while IFS= read -r name; do
        [[ "${name}" != /* && "${name}" != *\\* && "/${name}/" != *'/../'* ]] || return 1
    done <<<"${names}"
    listing="$(LC_ALL=C unzip -Z -l "${archive}" xray)" || return 1
    [[ "$(awk '$NF == "xray" && $1 ~ /^-/ {n++} END {print n+0}' <<<"${listing}")" == 1 ]] || return 1
    unzip -p "${archive}" xray >"${candidate}" && [[ -s "${candidate}" ]] && chmod 755 "${candidate}"
}

# Report only a fixed stage name, never raw unit/environment/configuration values.
function legacy_snapshot_error() {
    printf '[CentOS 7] 服务预检失败 / Service preflight failed [%s]; 未停止服务 / nothing stopped.\n' "$1" >&2
    return 1
}

function legacy_service_snapshot() {
    local binary="$1" config="$2" directory="$3" exec_start property output
    : >"${directory}/properties.before" || { legacy_snapshot_error 'write.properties'; return 1; }
    exec_start="$(xray_service_property ExecStart)" || { legacy_snapshot_error 'query.ExecStart'; return 1; }
    printf 'ExecStart=%s\n' "${exec_start}" >>"${directory}/properties.before" || { legacy_snapshot_error 'write.properties'; return 1; }
    [[ "${exec_start}" == "{ path=${binary} ; argv[]=${binary} run -config ${config} ; "* &&
        "${exec_start}" != *'} {'* && "${exec_start}" != *$'\n'* ]] || { legacy_snapshot_error 'unsupported.ExecStart'; return 1; }
    for property in EnvironmentFiles RootDirectory RootImage ExecStartPre ExecStartPost; do
        if [[ "${property}" == RootImage ]]; then
            output="$(xray_optional_root_image)" || { legacy_snapshot_error 'query.RootImage'; return 1; }
        else
            output="$(xray_service_property "${property}" optional)" || { legacy_snapshot_error "query.${property}"; return 1; }
        fi
        [[ -z "${output}" ]] || { legacy_snapshot_error "unsupported.${property}"; return 1; }
        printf '%s=%s\n' "${property}" "${output}" >>"${directory}/properties.before" || { legacy_snapshot_error 'write.properties'; return 1; }
    done
    output="$(xray_service_property WorkingDirectory optional)" || { legacy_snapshot_error 'query.WorkingDirectory'; return 1; }
    [[ -z "${output}" || "${output}" == / ]] || { legacy_snapshot_error 'unsupported.WorkingDirectory'; return 1; }
    service_user="$(xray_service_property User optional)" || { legacy_snapshot_error 'query.User'; return 1; }
    service_group="$(xray_service_property Group optional)" || { legacy_snapshot_error 'query.Group'; return 1; }
    service_user="${service_user:-root}"
    [[ "${service_user}" == root || "${service_user}" == xray ]] || { legacy_snapshot_error 'unsupported.User'; return 1; }
    [[ -z "${service_group}" || "${service_group}" == "${service_user}" ]] || { legacy_snapshot_error 'unsupported.Group'; return 1; }
    id "${service_user}" >/dev/null 2>&1 || { legacy_snapshot_error 'lookup.User'; return 1; }
    output="$(xray_service_property Environment optional)" || { legacy_snapshot_error 'query.Environment'; return 1; }
    asset_dir=/usr/local/share/xray
    if [[ -n "${output}" ]]; then
        [[ "${output}" =~ ^XRAY_LOCATION_ASSET=(/[A-Za-z0-9_./-]+)$ ]] || { legacy_snapshot_error 'unsupported.Environment'; return 1; }
        asset_dir="${BASH_REMATCH[1]}"
    fi
    printf 'User=%s\nGroup=%s\nAsset=%s\n' "${service_user}" "${service_group}" "${asset_dir}" >>"${directory}/properties.before" || { legacy_snapshot_error 'write.properties'; return 1; }
    systemctl cat xray --no-pager --full >"${directory}/unit.before" 2>/dev/null || { legacy_snapshot_error 'read.unit'; return 1; }
    cp -- "${config}" "${directory}/config.before" || { legacy_snapshot_error 'copy.config'; return 1; }
    # Assets are used by validation, so reject a concurrent change before commit.
    for property in geoip.dat geosite.dat; do
        [[ ! -e "${asset_dir}/${property}" ]] || sha256sum "${asset_dir}/${property}" || { legacy_snapshot_error "hash.${property}"; return 1; }
    done >"${directory}/assets.before"
}

function legacy_test_candidate() {
    local candidate="$1" config="$2"
    if [[ "${service_user}" == root ]]; then
        env -i PATH="${PATH}" XRAY_LOCATION_ASSET="${asset_dir}" "${candidate}" run -test -config "${config}" >/dev/null 2>&1
    else
        command -v runuser >/dev/null || return 1
        runuser -u "${service_user}" -- env -i PATH="${PATH}" XRAY_LOCATION_ASSET="${asset_dir}" "${candidate}" run -test -config "${config}" >/dev/null 2>&1
    fi
}

function legacy_replace_state() {
    local source="$1" staged
    staged="$(mktemp "${SCRIPT_CONFIG_PATH}.version.XXXXXX")" || return 1
    if ! cp --preserve=all -- "${SCRIPT_CONFIG_PATH}" "${staged}" ||
        ! cat "${source}" >"${staged}" || ! mv -f -- "${staged}" "${SCRIPT_CONFIG_PATH}"; then
        rm -f -- "${staged}"
        return 1
    fi
}

function legacy_upgrade_cleanup() {
    local status=$? restore_failed=0
    trap - EXIT HUP INT TERM
    if [[ "${swap_started:-0}" == 1 && "${upgrade_success:-0}" != 1 ]]; then
        # A failed start can leave the new process running/activating. Stop it
        # before restoring bytes; `start` alone would otherwise be a no-op.
        if [[ "${was_active}" == active ]]; then
            systemctl stop xray >/dev/null 2>&1 && [[ "$(xray_service_property ActiveState)" == inactive ]] || restore_failed=1
        else
            case "$(xray_service_property ActiveState)" in inactive | failed) ;; *) restore_failed=1 ;; esac
        fi
        if [[ "${restore_failed}" == 0 ]]; then
            if ! legacy_binary_replace "${work_dir}/binary.before" "${binary}" "${work_dir}/attributes.before" "${original_mode}" "${original_caps}" "${original_context}"; then
                restore_failed=1
            elif [[ "${was_active}" == active ]]; then
                systemctl start xray >/dev/null 2>&1 || restore_failed=1
                sleep 1
                [[ "$(xray_service_property ActiveState)" == active ]] || restore_failed=1
            fi
        fi
        if [[ "${state_write_started:-0}" == 1 ]]; then
            legacy_replace_state "${work_dir}/script.before" || restore_failed=1
        fi
        if [[ "${restore_failed}" == 1 ]]; then
            printf '回滚未完全成功，请人工检查；原核心备份 / Recovery incomplete; original binary backup: %s\n' "${work_dir}" >&2
            status=1
        else
            echo '已恢复原核心和原服务运行状态。 / Original core and service state restored.' >&2
        fi
    elif [[ "${stop_attempted:-0}" == 1 && "${upgrade_success:-0}" != 1 && "${was_active}" == active ]]; then
        # No replacement occurred; only restart after proving core/config/unit unchanged.
        if cmp -s "${work_dir}/binary.before" "${binary}" && cmp -s "${work_dir}/config.before" "${config}" &&
            systemctl cat xray --no-pager --full >"${work_dir}/unit.after" 2>/dev/null && cmp -s "${work_dir}/unit.before" "${work_dir}/unit.after"; then
            systemctl start xray >/dev/null 2>&1 && [[ "$(xray_service_property ActiveState)" == active ]] || restore_failed=1
        else
            restore_failed=1
        fi
        [[ "${restore_failed}" == 0 ]] || { echo '原服务未能安全恢复，请人工检查备份。 / Could not safely recover the previous service.' >&2; status=1; }
    fi
    [[ -z "${candidate:-}" ]] || rm -f -- "${candidate}"
    # Keep the private original-binary backup for deliberate rollback/manual review.
    [[ -z "${work_dir:-}" ]] || printf '原核心备份 / Original core backup: %s\n' "${work_dir}" >&2
    exit "${status}"
}

function legacy_upgrade_xray() {
    (
        set +x
        umask 077
        local version="${1:-}" binary config work_dir='' candidate='' was_active service_user service_group asset_dir
        local original_mode original_caps original_context old_hash current_hash reported property
        local stop_attempted=0 swap_started=0 upgrade_success=0 state_write_started=0
        trap legacy_upgrade_cleanup EXIT
        trap 'exit 129' HUP
        trap 'exit 130' INT
        trap 'exit 143' TERM
        [[ "${version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || legacy_error 'Invalid core version.'
        binary="$(legacy_binary_path)"; config="$(legacy_config_path)"
        [[ -f "${binary}" && -x "${binary}" && ! -L "${binary}" && -f "${config}" && ! -L "${config}" ]] || legacy_error '仅支持已有标准安装，缺少核心/配置；未停止服务。 / Existing standard binary and configuration required.'
        for property in curl unzip sha256sum getcap stat flock; do
            command -v "${property}" >/dev/null || legacy_error "Missing dependency: ${property}; install it from trusted OS repositories before retrying. No service was stopped."
        done
        [[ -d "${SCRIPT_CONFIG_DIR}" && ! -L "${SCRIPT_CONFIG_DIR}/config-edit.lock" && ! -L "${SCRIPT_CONFIG_DIR}/backups" ]] || legacy_error 'Unsafe backup/lock directory.'
        exec 9>"${SCRIPT_CONFIG_DIR}/config-edit.lock" && flock -n 9 || legacy_error 'Another configuration edit/upgrade is running.'
        was_active="$(xray_service_property ActiveState)" || legacy_error 'Cannot determine service state.'
        case "${was_active}" in active | inactive | failed) ;; *) legacy_error 'Service is changing state; retry later.' ;; esac
        mkdir -p "${SCRIPT_CONFIG_DIR}/backups" && chmod 700 "${SCRIPT_CONFIG_DIR}/backups" || legacy_error 'Cannot create private backups.'
        work_dir="$(mktemp -d "${SCRIPT_CONFIG_DIR}/backups/core.XXXXXX")" || legacy_error 'Cannot create private backup.'
        [[ -f "${SCRIPT_CONFIG_PATH}" && ! -L "${SCRIPT_CONFIG_PATH}" ]] && cp -- "${SCRIPT_CONFIG_PATH}" "${work_dir}/script.before" || legacy_error 'Cannot snapshot script state.'
        legacy_service_snapshot "${binary}" "${config}" "${work_dir}" || legacy_error '服务预检未通过，请查看上方阶段代码；未停止服务。 / Service preflight failed; see the stage above. Nothing stopped.'
        original_mode="$(stat -c %a "${binary}")" || legacy_error 'Cannot read original permissions.'
        original_caps="$(legacy_capabilities "${binary}")" || legacy_error 'Cannot inspect file capabilities.'
        original_context="$(legacy_context "${binary}")" || legacy_error 'Cannot inspect SELinux context.'
        cp --preserve=all -- "${binary}" "${work_dir}/binary.before" &&
            cp --attributes-only --preserve=all -- "${binary}" "${work_dir}/attributes.before" &&
            chmod 600 "${work_dir}/binary.before" "${work_dir}/attributes.before" || legacy_error 'Cannot preserve the original binary.'
        old_hash="$(sha256sum "${binary}")" || legacy_error 'Cannot fingerprint original binary.'
        candidate="$(mktemp "${binary}.candidate.XXXXXX")" || legacy_error 'Cannot stage candidate beside current binary.'
        legacy_download_core "${version}" "${work_dir}" "${candidate}" || legacy_error '官方下载/校验或归档安全检查失败；未停止服务。 / Official download, digest or archive validation failed; service unchanged.'
        legacy_apply_attributes "${work_dir}/attributes.before" "${candidate}" "${original_mode}" "${original_caps}" "${original_context}" || legacy_error 'Cannot preserve candidate SELinux context/capabilities; service unchanged.'
        reported="$("${candidate}" version 2>/dev/null)" || legacy_error 'New core cannot run on this CPU/kernel; service unchanged.'
        [[ "$(awk 'NR==1 {print "v" $2}' <<<"${reported}")" == "${version}" ]] || legacy_error 'Candidate version mismatch; service unchanged.'
        legacy_test_candidate "${candidate}" "${config}" || legacy_error '新核心无法通过现有配置检查；未停止服务。 / Candidate config test failed; original service untouched.'
        current_hash="$(sha256sum "${binary}")" || legacy_error 'Cannot recheck original binary.'
        [[ "${current_hash}" == "${old_hash}" && ! -L "${binary}" && ! -L "${config}" ]] && cmp -s "${work_dir}/binary.before" "${binary}" && cmp -s "${work_dir}/config.before" "${config}" || legacy_error 'Core/configuration changed concurrently.'
        systemctl cat xray --no-pager --full >"${work_dir}/unit.after" 2>/dev/null && cmp -s "${work_dir}/unit.before" "${work_dir}/unit.after" || legacy_error 'Service definition changed concurrently.'
        for property in geoip.dat geosite.dat; do
            [[ ! -e "${asset_dir}/${property}" ]] || sha256sum "${asset_dir}/${property}" || legacy_error 'Cannot recheck assets.'
        done >"${work_dir}/assets.after"
        cmp -s "${work_dir}/assets.before" "${work_dir}/assets.after" && [[ "$(xray_service_property ActiveState)" == "${was_active}" ]] || legacy_error 'Assets/service state changed concurrently.'
        mkdir "${work_dir}/check" && legacy_service_snapshot "${binary}" "${config}" "${work_dir}/check" || legacy_error 'Cannot recheck loaded service settings.'
        cmp -s "${work_dir}/properties.before" "${work_dir}/check/properties.before" &&
            cmp -s "${work_dir}/script.before" "${SCRIPT_CONFIG_PATH}" || legacy_error 'Service settings/script state changed concurrently.'
        if [[ "${was_active}" == active ]]; then
            stop_attempted=1
            systemctl stop xray >/dev/null 2>&1 && [[ "$(xray_service_property ActiveState)" == inactive ]] || legacy_error 'Could not safely stop service; aborting.'
        fi
        swap_started=1
        legacy_binary_replace "${candidate}" "${binary}" "${work_dir}/attributes.before" "${original_mode}" "${original_caps}" "${original_context}" || legacy_error 'Atomic binary replacement failed; recovering original.'
        reported="$("${binary}" version 2>/dev/null)" || legacy_error 'Installed core cannot run; rolling back.'
        [[ "$(awk 'NR==1 {print "v" $2}' <<<"${reported}")" == "${version}" ]] || legacy_error 'Installed version mismatch; rolling back.'
        if [[ "${was_active}" == active ]]; then
            systemctl start xray >/dev/null 2>&1 || legacy_error 'New core startup failed; rolling back.'
            sleep 1
            [[ "$(xray_service_property ActiveState)" == active ]] || legacy_error 'New core did not stay active; rolling back.'
        fi
        cmp -s "${work_dir}/script.before" "${SCRIPT_CONFIG_PATH}" || legacy_error 'Script state changed before version update; recovering core only.'
        jq --arg version "${version}" '.xray.version = $version' "${SCRIPT_CONFIG_PATH}" >"${work_dir}/script.after" || legacy_error 'Cannot prepare version metadata.'
        state_write_started=1
        legacy_replace_state "${work_dir}/script.after" || legacy_error 'Cannot save version metadata; rolling back.'
        upgrade_success=1
        printf 'CentOS 7 核心已更新至 %s；保留原配置、账号、服务及 GeoData。 / Core upgraded; original configuration, account, unit and GeoData preserved.\n' "${version}"
        [[ "${was_active}" == active ]] || echo '服务原本未运行，仍保持停止。 / Service remains stopped as before.'
    )
}
