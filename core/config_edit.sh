#!/usr/bin/env bash
# Targeted edits of the existing runtime JSON. This module has no entry point.
# Never invoke full-template generation from this path.

function config_edit_message() {
    local message
    message="$(jq -r --arg key "$1" '.config_edit[$key] // $key' <<<"${I18N_DATA:-null}" 2>/dev/null)" || message="$1"
    printf '%s\n' "${message}"
}

function config_edit_fail() {
    config_edit_message "$1" >&2
    exit 1
}

# A same-directory rename preserves the original file on a failed write.
function config_edit_replace() {
    local source="$1" destination="$2" mode="$3" owner="$4" group="$5" staged
    staged="$(mktemp "${destination}.edit.XXXXXX")" || return 1
    if ! cp -- "${source}" "${staged}" ||
        ! chmod "${mode}" "${staged}" || ! chown "${owner}:${group}" "${staged}" ||
        ! mv -f -- "${staged}" "${destination}"; then
        rm -f -- "${staged}"
        return 1
    fi
}

# Called only by the scoped subshell below, including on interrupts.
function config_edit_cleanup() {
    local status=$? rollback_failed=0 runtime_restored=0
    trap - EXIT HUP INT TERM
    if [[ "${commit_started:-0}" == 1 && "${edit_success:-0}" != 1 ]]; then
        if config_edit_replace "${backup_dir}/runtime.json" "${XRAY_CONFIG_PATH}" "${runtime_mode}" "${runtime_uid}" "${runtime_gid}"; then
            runtime_restored=1
        else
            rollback_failed=1
        fi
        config_edit_replace "${backup_dir}/script.json" "${SCRIPT_CONFIG_PATH}" "${script_mode}" "${script_uid}" "${script_gid}" || rollback_failed=1
        if [[ "${service_state}" == active && "${runtime_restored}" == 1 ]]; then
            if ! systemctl restart xray >/dev/null 2>&1; then
                rollback_failed=1
            else
                sleep 1
                systemctl -q is-active xray >/dev/null 2>&1 || rollback_failed=1
            fi
        fi
        if [[ "${rollback_failed}" == 1 ]]; then
            config_edit_message rollback_failed >&2
            printf '%s\n' "${backup_dir}" >&2
            status=1
        else
            config_edit_message rollback_done >&2
        fi
    fi
    [[ -z "${work_dir:-}" ]] || rm -rf -- "${work_dir}"
    exit "${status}"
}

function handler_change_xray_field() {
    (
        # Avoid exposing credentials when the caller enabled shell tracing.
        set +x
        umask 077
        local action="${1:-}" script_json runtime_json profile inbound_tag protocol
        local inbound_index client_index='' matches value='' answer='' short_ids_json=''
        local private_key='' public_key='' hash32='' key_output='' item edit_values_json=''
        local old_runtime_hash old_script_hash current_hash service_state current_state
        local runtime_mode runtime_uid runtime_gid script_mode script_uid script_gid
        local work_dir='' backup_dir='' commit_started=0 edit_success=0
        local -a short_ids=()
        trap config_edit_cleanup EXIT
        trap 'exit 129' HUP
        trap 'exit 130' INT
        trap 'exit 143' TERM

        case "${action}" in uuid | reality-key | short-id | port) ;; *) config_edit_fail unsupported_mode ;; esac
        [[ -f "${XRAY_CONFIG_PATH}" && ! -L "${XRAY_CONFIG_PATH}" &&
            -f "${SCRIPT_CONFIG_PATH}" && ! -L "${SCRIPT_CONFIG_PATH}" ]] || config_edit_fail state_mismatch
        old_runtime_hash="$(sha256sum "${XRAY_CONFIG_PATH}")" || config_edit_fail state_mismatch
        old_runtime_hash="${old_runtime_hash%% *}"
        old_script_hash="$(sha256sum "${SCRIPT_CONFIG_PATH}")" || config_edit_fail state_mismatch
        old_script_hash="${old_script_hash%% *}"
        script_json="$(jq -e 'select(type == "object")' "${SCRIPT_CONFIG_PATH}" 2>/dev/null)" || config_edit_fail state_mismatch
        runtime_json="$(jq -e 'select(type == "object" and (.inbounds | type == "array"))' "${XRAY_CONFIG_PATH}" 2>/dev/null)" || config_edit_fail state_mismatch
        profile="$(jq -r '.xray.tag // empty | ascii_downcase' <<<"${script_json}" 2>/dev/null)" || config_edit_fail unsupported_mode
        case "${profile}" in
        vision | fallback | sni) inbound_tag='VLESS-Vision-REALITY'; protocol='vless' ;;
        xhttp) inbound_tag='VLESS-XHTTP-REALITY'; protocol='vless' ;;
        mkcp) inbound_tag='VLESS-mKCP'; protocol='vless' ;;
        trojan) inbound_tag='Trojan-XHTTP-REALITY'; protocol='trojan' ;;
        *) config_edit_fail unsupported_mode ;;
        esac
        [[ "${action}" != port || "${profile}" != sni ]] || config_edit_fail unsupported_sni_port
        [[ "${action}" != uuid || "${protocol}" == vless ]] || config_edit_fail unsupported_uuid
        if [[ "${action}" == reality-key || "${action}" == short-id ]]; then
            [[ "${profile}" != mkcp ]] || config_edit_fail unsupported_reality
        fi
        matches="$(jq -c --arg tag "${inbound_tag}" '[.inbounds | to_entries[] | select(.value.tag == $tag) | .key]' <<<"${runtime_json}" 2>/dev/null)" || config_edit_fail managed_inbound_missing
        [[ "$(jq length <<<"${matches}")" == 1 ]] || config_edit_fail managed_inbound_missing
        inbound_index="$(jq -r '.[0]' <<<"${matches}")"
        jq -e --argjson i "${inbound_index}" --arg protocol "${protocol}" '.inbounds[$i].protocol == $protocol' <<<"${runtime_json}" >/dev/null 2>&1 || config_edit_fail state_mismatch

        case "${action}" in
        uuid)
            value="$(jq -r '.xray.uuid // empty' <<<"${script_json}")"
            [[ "${value}" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] || config_edit_fail state_mismatch
            matches="$(jq -sc --argjson i "${inbound_index}" '.[0] as $runtime | (.[1].xray.uuid | ascii_downcase) as $id | [$runtime.inbounds[$i].settings.clients | to_entries[] | select((.value.id // "" | ascii_downcase) == $id) | .key]' <<<"${runtime_json}"$'\n'"${script_json}" 2>/dev/null)" || config_edit_fail managed_client_missing
            [[ "$(jq length <<<"${matches}")" == 1 ]] || config_edit_fail managed_client_missing
            client_index="$(jq -r '.[0]' <<<"${matches}")"
            config_edit_message uuid_prompt >&2
            IFS= read -rs value || { config_edit_message cancelled >&2; exit 0; }
            printf '\n' >&2
            [[ -n "${value}" ]] || { config_edit_message cancelled >&2; exit 0; }
            [[ "${value}" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] || config_edit_fail invalid_uuid
            value="${value,,}"
            edit_values_json="$(printf '{"value":"%s"}' "${value}")"
            jq -se --argjson i "${inbound_index}" --argjson c "${client_index}" '.[0] as $runtime | .[1].value as $id | all($runtime.inbounds[$i].settings.clients | to_entries[]; .key == $c or (.value.id // "" | ascii_downcase) != $id)' <<<"${runtime_json}"$'\n'"${edit_values_json}" >/dev/null 2>&1 || config_edit_fail duplicate_uuid
            [[ "${value}" != "$(jq -r '.xray.uuid | ascii_downcase' <<<"${script_json}")" ]] || { config_edit_message unchanged >&2; exit 0; }
            ;;
        reality-key | short-id)
            jq -se --argjson i "${inbound_index}" '.[1] as $state | .[0].inbounds[$i].streamSettings | .security == "reality" and (.realitySettings | type == "object") and (.realitySettings.privateKey | type == "string" and length > 0) and .realitySettings.privateKey == $state.xray.privateKey' <<<"${runtime_json}"$'\n'"${script_json}" >/dev/null 2>&1 || config_edit_fail unsupported_reality
            if [[ "${action}" == short-id ]]; then
                config_edit_message short_id_prompt >&2
                IFS= read -rs value || { config_edit_message cancelled >&2; exit 0; }
                printf '\n' >&2
                [[ -n "${value}" ]] || { config_edit_message cancelled >&2; exit 0; }
                [[ "${value}" != ,* && "${value}" != *, && "${value}" != *,,* ]] || config_edit_fail invalid_short_id
                IFS=, read -ra short_ids <<<"${value}"
                local n
                for n in "${!short_ids[@]}"; do
                    item="${short_ids[$n]}"
                    item="${item#"${item%%[![:space:]]*}"}"
                    item="${item%"${item##*[![:space:]]}"}"
                    [[ "${item}" =~ ^([0-9a-fA-F]{2}){1,8}$ ]] || config_edit_fail invalid_short_id
                    short_ids[$n]="${item,,}"
                done
                short_ids_json="$(printf '%s\n' "${short_ids[@]}" | jq -Rsc 'split("\n")[:-1]')" || config_edit_fail invalid_short_id
                jq -e 'length > 0 and (length == (unique | length))' <<<"${short_ids_json}" >/dev/null || config_edit_fail invalid_short_id
                [[ "${short_ids_json}" != "$(jq -c --argjson i "${inbound_index}" '.inbounds[$i].streamSettings.realitySettings.shortIds' <<<"${runtime_json}")" ]] || { config_edit_message unchanged >&2; exit 0; }
            fi
            ;;
        port)
            jq -se --argjson i "${inbound_index}" '.[1] as $state | .[0] | (.inbounds[$i].port | tostring | test("^[0-9]{1,5}$")) and (.inbounds[$i].port | tonumber) == ($state.xray.port | tonumber) and ((.inbounds[$i].listen // "0.0.0.0") | startswith("/") or startswith("@") | not)' <<<"${runtime_json}"$'\n'"${script_json}" >/dev/null 2>&1 || config_edit_fail state_mismatch
            config_edit_message port_prompt >&2
            IFS= read -r value || { config_edit_message cancelled >&2; exit 0; }
            [[ -n "${value}" ]] || { config_edit_message cancelled >&2; exit 0; }
            [[ "${value}" =~ ^[0-9]{1,5}$ ]] || config_edit_fail invalid_port
            value=$((10#${value}))
            ((value >= 1 && value <= 65535)) || config_edit_fail invalid_port
            [[ "${value}" != "$(jq -r '.xray.port | tonumber' <<<"${script_json}")" ]] || { config_edit_message unchanged >&2; exit 0; }
            jq -e --argjson i "${inbound_index}" --arg port "${value}" 'all(.inbounds | to_entries[]; .key == $i or (.value.port | tostring) != $port)' <<<"${runtime_json}" >/dev/null 2>&1 || config_edit_fail port_conflict
            ;;
        esac
        config_edit_message warning >&2
        config_edit_message confirm >&2
        IFS= read -r answer || answer=''
        [[ "${answer}" == YES ]] || { config_edit_message cancelled >&2; exit 0; }

        [[ -d "${SCRIPT_CONFIG_DIR}" && ! -L "${SCRIPT_CONFIG_DIR}/config-edit.lock" ]] || config_edit_fail backup_root_invalid
        exec 9>"${SCRIPT_CONFIG_DIR}/config-edit.lock" || config_edit_fail busy
        flock -n 9 || config_edit_fail busy
        # Reject changes made by another menu/process while the prompt was open.
        current_hash="$(sha256sum "${XRAY_CONFIG_PATH}")" || config_edit_fail concurrent_change
        [[ "${current_hash%% *}" == "${old_runtime_hash}" ]] || config_edit_fail concurrent_change
        current_hash="$(sha256sum "${SCRIPT_CONFIG_PATH}")" || config_edit_fail concurrent_change
        [[ "${current_hash%% *}" == "${old_script_hash}" ]] || config_edit_fail concurrent_change
        service_state="$(systemctl show --property=ActiveState --value xray 2>/dev/null)" || config_edit_fail service_state_failed
        case "${service_state}" in active | inactive | failed) ;; *) config_edit_fail service_state_failed ;; esac
        read -r runtime_mode runtime_uid runtime_gid < <(stat -c '%a %u %g' "${XRAY_CONFIG_PATH}")
        read -r script_mode script_uid script_gid < <(stat -c '%a %u %g' "${SCRIPT_CONFIG_PATH}")
        [[ -n "${runtime_gid}" && -n "${script_gid}" ]] || config_edit_fail backup_failed
        [[ ! -L "${SCRIPT_CONFIG_DIR}/backups" ]] || config_edit_fail backup_root_invalid
        mkdir -p "${SCRIPT_CONFIG_DIR}/backups" && chmod 700 "${SCRIPT_CONFIG_DIR}/backups" || config_edit_fail backup_failed
        backup_dir="$(mktemp -d "${SCRIPT_CONFIG_DIR}/backups/edit.XXXXXX")" || config_edit_fail backup_failed
        work_dir="$(mktemp -d "${SCRIPT_CONFIG_DIR}/.edit-work.XXXXXX")" || config_edit_fail backup_failed
        cp -- "${XRAY_CONFIG_PATH}" "${backup_dir}/runtime.json" &&
            cp -- "${SCRIPT_CONFIG_PATH}" "${backup_dir}/script.json" &&
            chmod 600 "${backup_dir}/runtime.json" "${backup_dir}/script.json" || config_edit_fail backup_failed
        current_hash="$(sha256sum "${backup_dir}/runtime.json")" || config_edit_fail backup_failed
        [[ "${current_hash%% *}" == "${old_runtime_hash}" ]] || config_edit_fail concurrent_change
        current_hash="$(sha256sum "${backup_dir}/script.json")" || config_edit_fail backup_failed
        [[ "${current_hash%% *}" == "${old_script_hash}" ]] || config_edit_fail concurrent_change
        jq -n --arg action "${action}" --arg state "${service_state}" --arg runtime_mode "${runtime_mode}" --arg runtime_uid "${runtime_uid}" --arg runtime_gid "${runtime_gid}" --arg script_mode "${script_mode}" --arg script_uid "${script_uid}" --arg script_gid "${script_gid}" '{action:$action,service_state:$state,runtime:{mode:$runtime_mode,uid:$runtime_uid,gid:$runtime_gid},script:{mode:$script_mode,uid:$script_uid,gid:$script_gid}}' >"${backup_dir}/metadata.json" || config_edit_fail backup_failed

        if [[ "${action}" == reality-key ]]; then
            key_output="$(xray x25519 2>/dev/null)" || config_edit_fail key_failed
            private_key="$(awk -F: '{label=tolower($1); gsub(/[[:space:]]/,"",label); if(label=="privatekey") {gsub(/[[:space:]]/,"",$2);print $2}}' <<<"${key_output}")"
            public_key="$(awk -F: '{label=tolower($1); gsub(/[[:space:]]/,"",label); if(label=="publickey" || label=="password" || label=="password(publickey)") {gsub(/[[:space:]]/,"",$2);print $2}}' <<<"${key_output}")"
            hash32="$(awk -F: '{label=tolower($1); gsub(/[[:space:]]/,"",label); if(label=="hash32") {gsub(/[[:space:]]/,"",$2);print $2}}' <<<"${key_output}")"
            [[ "${private_key}" =~ ^[A-Za-z0-9_-]{43}$ && "${public_key}" =~ ^[A-Za-z0-9_-]{43}$ &&
                ( -z "${hash32}" || "${hash32}" =~ ^[A-Za-z0-9_-]{43}$ ) ]] || config_edit_fail key_failed
            [[ "${private_key}" != "$(jq -r '.xray.privateKey' <<<"${script_json}")" ]] || config_edit_fail key_failed
        fi
        case "${action}" in
        uuid) edit_values_json="$(printf '{"value":"%s"}' "${value}")" ;;
        reality-key) edit_values_json="$(printf '{"private":"%s","public":"%s","hash":"%s"}' "${private_key}" "${public_key}" "${hash32}")" ;;
        short-id) edit_values_json="{\"value\":${short_ids_json}}" ;;
        port) edit_values_json="{\"value\":${value}}" ;;
        esac
        # Credentials travel only through stdin/private files, never process argv.
        jq -s --arg action "${action}" --argjson i "${inbound_index}" --argjson c "${client_index:-0}" '
            .[0] as $runtime | .[1] as $state | .[2] as $edit |
            if $action == "uuid" then
                {runtime: ($runtime | .inbounds[$i].settings.clients[$c].id = $edit.value),
                 script: ($state | .xray.uuid = $edit.value)}
            elif $action == "reality-key" then
                {runtime: ($runtime | .inbounds[$i].streamSettings.realitySettings.privateKey = $edit.private),
                 script: ($state | .xray.privateKey = $edit.private | .xray.publicKey = $edit.public | .xray.hash32 = $edit.hash)}
            elif $action == "short-id" then
                {runtime: ($runtime | .inbounds[$i].streamSettings.realitySettings.shortIds = $edit.value),
                 script: ($state | .xray.shortIds = $edit.value)}
            else
                {runtime: ($runtime | .inbounds[$i].port = $edit.value),
                 script: ($state | .xray.port = $edit.value)}
            end
        ' <<<"${runtime_json}"$'\n'"${script_json}"$'\n'"${edit_values_json}" >"${work_dir}/proposal.json" 2>/dev/null || config_edit_fail internal_error
        jq '.runtime' "${work_dir}/proposal.json" >"${work_dir}/runtime.json" &&
            jq '.script' "${work_dir}/proposal.json" >"${work_dir}/script.json" || config_edit_fail internal_error
        # Do not echo diagnostics: a third-party config error may contain secrets.
        xray run -test -format json -config "${work_dir}/runtime.json" >/dev/null 2>&1 || config_edit_fail test_failed
        cmp -s "${backup_dir}/runtime.json" "${XRAY_CONFIG_PATH}" &&
            cmp -s "${backup_dir}/script.json" "${SCRIPT_CONFIG_PATH}" &&
            [[ ! -L "${XRAY_CONFIG_PATH}" && ! -L "${SCRIPT_CONFIG_PATH}" ]] || config_edit_fail concurrent_change
        current_state="$(systemctl show --property=ActiveState --value xray 2>/dev/null)" || config_edit_fail service_state_failed
        [[ "${current_state}" == "${service_state}" ]] || config_edit_fail concurrent_change
        commit_started=1
        config_edit_replace "${work_dir}/runtime.json" "${XRAY_CONFIG_PATH}" "${runtime_mode}" "${runtime_uid}" "${runtime_gid}" || config_edit_fail write_failed
        config_edit_replace "${work_dir}/script.json" "${SCRIPT_CONFIG_PATH}" "${script_mode}" "${script_uid}" "${script_gid}" || config_edit_fail write_failed
        if [[ "${service_state}" == active ]]; then
            systemctl restart xray >/dev/null 2>&1 || config_edit_fail restart_failed
            sleep 1
            systemctl -q is-active xray >/dev/null 2>&1 || config_edit_fail restart_failed
        fi
        edit_success=1
        config_edit_message success >&2
        config_edit_message backup_saved >&2
        printf '%s\n' "${backup_dir}" >&2
        case "${action}" in
        uuid) config_edit_message client_uuid >&2; printf '%s\n' "${value}" >&2 ;;
        reality-key) config_edit_message client_reality_key >&2; printf '%s\n' "${public_key}" >&2 ;;
        short-id) config_edit_message client_short_id >&2; printf '%s\n' "${short_ids[@]}" >&2 ;;
        port) config_edit_message client_port >&2; printf '%s\n' "${value}" >&2; config_edit_message client_port_firewall >&2 ;;
        esac
        config_edit_message client_keep_others >&2
        config_edit_message client_reconnect >&2
        [[ "${service_state}" == active ]] || config_edit_message service_still_stopped >&2
        config_edit_message share_hint >&2
    )
}
