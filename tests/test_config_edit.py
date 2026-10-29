#!/usr/bin/env python3
"""Isolated tests for surgical configuration editing; no live service or keys.

All paths are temporary. Only real function definitions are evaluated; command
entry points are never sourced. Xray and systemd are mocked with public vectors.
"""

import copy
import fcntl
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from test_regressions import ROOT, functions_from, named_function

OLD_UUID = '00000000-0000-4000-8000-000000000001'
NEW_UUID = '00000000-0000-4000-8000-000000000002'
OTHER_UUID = '00000000-0000-4000-8000-000000000003'
FALLBACK_UUID = '00000000-0000-4000-8000-000000000004'
# Public RFC 7748 test vectors, never generated credentials.
OLD_PRIVATE = 'dwdtCnMYpX08FsFyUbJmRd9ML4frwJkqsXf7pR25LCo'
OLD_PUBLIC = 'hSDwCYkwp1R0i33ctD73Wg2_Og0mOBr066SpjqqbTmo'
NEW_PRIVATE = 'XasIfmJKikt54X-Lg4AO5m87sSkmGLb9HC-LJ_8i4Os'
NEW_PUBLIC = '3p7bfXt9wbTTW2HC7OQ1Nz-DQ8hbeGdNrfx-FG-IK08'


class ConfigEditCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='xray-config-edit-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.state_path = self.home / 'state.json'
        self.runtime_path = self.home / 'runtime.json'
        self.calls_path = self.home / 'calls'
        self.env = os.environ.copy()
        self.env.update(
            HOME=str(self.home), TMPDIR=str(self.home),
            SCRIPT_CONFIG_DIR=str(self.home / 'script-state'),
            SCRIPT_CONFIG_PATH=str(self.state_path),
            XRAY_CONFIG_PATH=str(self.runtime_path),
            CALLS_FILE=str(self.calls_path),
            SERVICE_ACTIVE='1', MOCK_RESTART_FAILURES='0', MOCK_TEST_EXIT='0',
        )
        Path(self.env['SCRIPT_CONFIG_DIR']).mkdir()
        self.fixture('Vision')

    def fixture(self, profile):
        self.state = json.loads((ROOT / 'config.json').read_text())
        self.state['language'] = 'en'
        self.state['xray'].update(
            tag=profile, version='v26.10.21', port=443,
            uuid=OLD_UUID, fallback=FALLBACK_UUID,
            privateKey=OLD_PRIVATE, publicKey=OLD_PUBLIC, hash32='old-public-hash',
            shortIds=['0123456789abcdef'], target='example.com', serverNames=['example.com'],
            path='/keep-this-path', trojan='public-fixture-not-a-credential', kcp='keep-kcp',
        )
        self.state['nginx'].update(domain='keep-domain.example', custom_sites=[{'domain': 'keep-site.example'}])
        self.state['custom_test_extension'] = {'keep': ['settings', 1]}
        self.runtime = json.loads((ROOT / f'config/xray/{profile}.json').read_text())
        self.runtime['log'] = {'loglevel': 'warning'}
        self.runtime['dns'] = {'servers': ['localhost']}
        self.runtime['custom_test_extension'] = {'keep': 'runtime'}
        for inbound in self.runtime['inbounds']:
            if inbound['tag'] == 'api':
                continue
            inbound['custom_test_extension'] = {'keep': 'inbound'}
            for client in inbound['settings']['clients']:
                if 'id' in client:
                    client['id'] = FALLBACK_UUID if (profile in ('Fallback', 'SNI') and inbound['streamSettings']['network'] == 'xhttp') else OLD_UUID
                if 'password' in client:
                    client['password'] = self.state['xray']['trojan']
            reality = inbound.get('streamSettings', {}).get('realitySettings')
            if reality:
                reality.update(privateKey=OLD_PRIVATE, serverNames=['example.com'], shortIds=['0123456789abcdef'])
                if profile != 'SNI':
                    reality['target'] = 'example.com:443'
        # A second real client must never be overwritten by a script-owned edit.
        if profile != 'Trojan':
            extra = copy.deepcopy(self.runtime['inbounds'][1]['settings']['clients'][0])
            extra.update(id=OTHER_UUID, email='unrelated@example.invalid')
            self.runtime['inbounds'][1]['settings']['clients'].append(extra)
        # An entirely unrelated inbound must remain byte-for-byte equivalent JSON.
        self.runtime['inbounds'].append({
            'tag': 'unrelated-inbound', 'listen': '127.0.0.1', 'port': 12345,
            'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': False},
        })
        self.save_fixture()

    def save_fixture(self):
        self.state_path.write_text(json.dumps(self.state))
        self.runtime_path.write_text(json.dumps(self.runtime))
        self.state_path.chmod(0o600)
        self.runtime_path.chmod(0o640)

    def run_edit(self, field, stdin='', extra=''):
        source = 'source ' + shlex.quote(str(ROOT / 'core/config_edit.sh'))
        harness = f"""
SCRIPT_CONFIG=$(cat "$SCRIPT_CONFIG_PATH")
I18N_DATA='{{}}'; GREEN=; RED=; YELLOW=; NC=
declare -A CONFIG_DATA=()
_error() {{ printf '%s\\n' "$*" >&2; exit 1; }}
sleep() {{ :; }}
curl() {{ echo 'unexpected curl' >&2; return 98; }}
handler_xray_config() {{ echo 'TEMPLATE_REGEN' >> "$CALLS_FILE"; return 98; }}
handler_script_config() {{ echo 'STATE_REGEN' >> "$CALLS_FILE"; return 98; }}
xray() {{
    printf 'xray %s\\n' "$1" >> "$CALLS_FILE"
    case "$1" in
    run)
        local candidate=''
        while [[ $# -gt 0 ]]; do
            case "$1" in -config|-c) candidate=$2; shift ;; esac
            shift
        done
        [[ -n "$candidate" && "$candidate" == "$HOME/"* ]] || return 97
        jq -e 'type == "object"' "$candidate" >/dev/null || return 96
        return "$MOCK_TEST_EXIT"
        ;;
    x25519)
        printf 'PrivateKey: {NEW_PRIVATE}\\nPassword: {NEW_PUBLIC}\\nHash32: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\\n'
        ;;
    version) printf 'Xray 26.10.21 (public fixture)\\n' ;;
    *) echo 'unexpected xray command' >&2; return 98 ;;
    esac
}}
systemctl() {{
    printf 'systemctl %s\\n' "$*" >> "$CALLS_FILE"
    case " $* " in
    *' show '*)
        if [[ -n "${{MOCK_SERVICE_STATE:-}}" ]]; then printf 'ActiveState=%s\\n' "$MOCK_SERVICE_STATE"
        elif [[ "$SERVICE_ACTIVE" == 1 ]]; then printf 'ActiveState=active\\n'
        else printf 'ActiveState=inactive\\n'; fi
        ;;
    *' is-active '*) [[ "$SERVICE_ACTIVE" == 1 ]] ;;
    *' restart '*)
        local count=0
        [[ ! -f "$HOME/restart-count" ]] || count=$(cat "$HOME/restart-count")
        count=$((count + 1))
        printf '%s' "$count" > "$HOME/restart-count"
        (( count > MOCK_RESTART_FAILURES ))
        ;;
    *) echo 'unexpected systemctl command' >&2; return 98 ;;
    esac
}}
"""
        script = source + '\n' + harness + '\n' + extra + '\nhandler_change_xray_field ' + shlex.quote(field)
        return subprocess.run(['bash', '--noprofile', '--norc', '-c', script], input=stdin,
                              env=self.env, cwd=self.home, text=True, capture_output=True, timeout=20)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_unchanged(self, original_state=None, original_runtime=None):
        self.assertEqual(json.loads(self.state_path.read_text()), self.state if original_state is None else original_state)
        self.assertEqual(json.loads(self.runtime_path.read_text()), self.runtime if original_runtime is None else original_runtime)

    def calls(self):
        return self.calls_path.read_text() if self.calls_path.exists() else ''


class SurgicalEditTests(ConfigEditCase):
    def test_uuid_changes_only_owned_client_and_script_uuid(self):
        result = self.run_edit('uuid', NEW_UUID + '\nYES\n')
        self.assert_success(result)
        state, runtime = copy.deepcopy(self.state), copy.deepcopy(self.runtime)
        state['xray']['uuid'] = NEW_UUID
        runtime['inbounds'][1]['settings']['clients'][0]['id'] = NEW_UUID
        self.assert_unchanged(state, runtime)
        self.assertNotIn('TEMPLATE_REGEN', self.calls())
        self.assertNotIn('STATE_REGEN', self.calls())
        self.assertIn('xray run', self.calls())
        self.assertIn('restart', self.calls())

    def test_uuid_preserves_separate_fallback_account(self):
        self.fixture('Fallback')
        result = self.run_edit('uuid', NEW_UUID + '\nYES\n')
        self.assert_success(result)
        state, runtime = copy.deepcopy(self.state), copy.deepcopy(self.runtime)
        state['xray']['uuid'] = NEW_UUID
        runtime['inbounds'][1]['settings']['clients'][0]['id'] = NEW_UUID
        self.assert_unchanged(state, runtime)
        self.assertEqual(json.loads(self.state_path.read_text())['xray']['fallback'], FALLBACK_UUID)

    def test_short_id_changes_only_reality_short_ids(self):
        result = self.run_edit('short-id', '01,0123456789abcdef\nYES\n')
        self.assert_success(result)
        state, runtime = copy.deepcopy(self.state), copy.deepcopy(self.runtime)
        expected = ['01', '0123456789abcdef']
        state['xray']['shortIds'] = expected
        runtime['inbounds'][1]['streamSettings']['realitySettings']['shortIds'] = expected
        self.assert_unchanged(state, runtime)

    def test_port_changes_only_owned_listener_and_state(self):
        result = self.run_edit('port', '8443\nYES\n')
        self.assert_success(result)
        state, runtime = copy.deepcopy(self.state), copy.deepcopy(self.runtime)
        state['xray']['port'] = 8443
        runtime['inbounds'][1]['port'] = 8443
        self.assert_unchanged(state, runtime)
        self.assertNotIn('firewall', self.calls())

    def test_port_rejects_sni_instead_of_altering_nginx(self):
        self.fixture('SNI')
        result = self.run_edit('port', '8443\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        self.assertNotIn('restart', self.calls())

    def test_cancelled_confirmation_leaves_everything_unchanged(self):
        for field, value in [('uuid', NEW_UUID), ('short-id', 'abcd'), ('port', '8443')]:
            with self.subTest(field=field):
                result = self.run_edit(field, value + '\nn\n')
                self.assert_success(result)
                self.assert_unchanged()
                self.assertNotIn('xray run', self.calls())
                self.assertNotIn('restart', self.calls())

    def test_empty_input_cancels_without_random_defaults(self):
        for field in ['uuid', 'short-id', 'port']:
            with self.subTest(field=field):
                result = self.run_edit(field, '\n')
                self.assert_success(result)
                self.assert_unchanged()
                self.assertNotIn('xray ', self.calls())
                self.assertNotIn('restart', self.calls())

    def test_invalid_values_rejected_without_write(self):
        values = {
            'uuid': ['not-a-uuid', '00000000-0000-4000-8000-00000000000g'],
            'short-id': ['a', 'zz', '0123456789abcdef01', 'ab,,cd', ',ab', 'ab,', 'ab cd', 'ab\tcd'],
            'port': ['0', '65536', '-1', 'abc'],
        }
        for field, candidates in values.items():
            for value in candidates:
                with self.subTest(field=field, value=value):
                    result = self.run_edit(field, value + '\nYES\n')
                    self.assertNotEqual(result.returncode, 0)
                    self.assert_unchanged()
                    self.assertNotIn('restart', self.calls())

    def test_owned_uuid_missing_aborts_instead_of_replacing_other_client(self):
        self.runtime['inbounds'][1]['settings']['clients'][0]['id'] = NEW_UUID
        self.save_fixture()
        result = self.run_edit('uuid', OTHER_UUID + '\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()

    def test_duplicate_managed_tag_aborts_ambiguous_edit(self):
        self.runtime['inbounds'].append(copy.deepcopy(self.runtime['inbounds'][1]))
        self.save_fixture()
        result = self.run_edit('uuid', NEW_UUID + '\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()

    def test_duplicate_owned_uuid_aborts_ambiguous_edit(self):
        self.runtime['inbounds'][1]['settings']['clients'].append(
            copy.deepcopy(self.runtime['inbounds'][1]['settings']['clients'][0]))
        self.save_fixture()
        result = self.run_edit('uuid', NEW_UUID + '\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()

    def test_validation_failure_never_commits_candidate(self):
        self.env['MOCK_TEST_EXIT'] = '23'
        result = self.run_edit('port', '8443\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        self.assertNotIn('restart', self.calls())

    def test_inactive_service_is_not_started(self):
        self.env['SERVICE_ACTIVE'] = '0'
        result = self.run_edit('port', '8443\nYES\n')
        self.assert_success(result)
        self.assertNotIn(' restart ', self.calls())
        self.assertNotIn(' start ', self.calls())
        self.assertNotIn(' enable ', self.calls())

    def test_restart_failure_restores_both_files_and_restarts_original(self):
        self.env['MOCK_RESTART_FAILURES'] = '1'
        result = self.run_edit('port', '8443\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        self.assertEqual((self.home / 'restart-count').read_text(), '2')

    def test_second_atomic_write_failure_restores_both_files(self):
        extra = r"""
eval "$(declare -f config_edit_replace | sed '1s/config_edit_replace/real_config_edit_replace/')"
config_edit_replace() {
    local count=0
    [[ ! -f "$HOME/write-count" ]] || count=$(cat "$HOME/write-count")
    count=$((count + 1))
    printf '%s' "$count" > "$HOME/write-count"
    [[ "$count" != 2 ]] || return 1
    real_config_edit_replace "$@"
}
"""
        result = self.run_edit('port', '8443\nYES\n', extra)
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        self.assertEqual((self.home / 'restart-count').read_text(), '1')

    def test_rotation_cancel_does_not_generate_or_write(self):
        result = self.run_edit('reality-key', 'n\n')
        self.assert_success(result)
        self.assert_unchanged()
        self.assertNotIn('x25519', self.calls())

    def test_reality_rotation_only_changes_key_fields_and_does_not_log_keys(self):
        result = self.run_edit('reality-key', 'YES\n')
        self.assert_success(result)
        state, runtime = copy.deepcopy(self.state), copy.deepcopy(self.runtime)
        state['xray'].update(privateKey=NEW_PRIVATE, publicKey=NEW_PUBLIC, hash32='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA')
        runtime['inbounds'][1]['streamSettings']['realitySettings']['privateKey'] = NEW_PRIVATE
        self.assert_unchanged(state, runtime)
        output = result.stdout + result.stderr
        for secret in [OLD_PRIVATE, NEW_PRIVATE, OLD_UUID, NEW_UUID]:
            self.assertNotIn(secret, output)
        self.assertIn('xray x25519', self.calls())


    def test_backup_files_are_private_complete_and_original(self):
        original_state = self.state_path.read_bytes()
        original_runtime = self.runtime_path.read_bytes()
        result = self.run_edit('port', '8443\nYES\n')
        self.assert_success(result)
        root = Path(self.env['SCRIPT_CONFIG_DIR']) / 'backups'
        backups = list(root.glob('edit.*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o700)
        for name in ['runtime.json', 'script.json', 'metadata.json']:
            with self.subTest(name=name):
                self.assertEqual((backups[0] / name).stat().st_mode & 0o777, 0o600)
                json.loads((backups[0] / name).read_text())
        self.assertEqual((backups[0] / 'runtime.json').read_bytes(), original_runtime)
        self.assertEqual((backups[0] / 'script.json').read_bytes(), original_state)
        self.assertEqual(self.runtime_path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)

    def test_transitional_service_state_is_not_modified(self):
        self.env['MOCK_SERVICE_STATE'] = 'activating'
        result = self.run_edit('port', '8443\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        self.assertNotIn('restart', self.calls())

    def test_failed_rollback_restart_is_reported(self):
        self.env['MOCK_RESTART_FAILURES'] = '2'
        result = self.run_edit('port', '8443\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        self.assertEqual((self.home / 'restart-count').read_text(), '2')
        self.assertRegex(result.stdout + result.stderr, r'(?i)rollback[_ -]?(?:failed|failure)')

    def test_matching_owned_client_is_not_assumed_to_be_first(self):
        clients = self.runtime['inbounds'][1]['settings']['clients']
        clients.reverse()
        self.save_fixture()
        result = self.run_edit('uuid', NEW_UUID + '\nYES\n')
        self.assert_success(result)
        state, runtime = copy.deepcopy(self.state), copy.deepcopy(self.runtime)
        state['xray']['uuid'] = NEW_UUID
        runtime['inbounds'][1]['settings']['clients'][1]['id'] = NEW_UUID
        self.assert_unchanged(state, runtime)

    def test_duplicate_short_ids_are_rejected(self):
        result = self.run_edit('short-id', 'ab,ab\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()

    def test_shell_metacharacters_are_data_not_commands(self):
        marker = self.home / 'injected'
        for field, value in [
            ('uuid', f'$(touch {marker})'),
            ('uuid', f'bad\n$(touch {marker})'),
            ('short-id', f'ab; touch {marker}'),
            ('short-id', f'`touch {marker}`'),
            ('port', f'$(touch {marker})'),
            ('port', '999999999999999999999999999999999999'),
        ]:
            with self.subTest(field=field, value=value):
                result = self.run_edit(field, value + '\nYES\n')
                self.assertNotEqual(result.returncode, 0)
                self.assert_unchanged()
                self.assertFalse(marker.exists())

    def test_leading_zero_port_is_rejected_or_treated_as_decimal(self):
        result = self.run_edit('port', '01024\nYES\n')
        if result.returncode == 0:
            self.assertEqual(json.loads(self.runtime_path.read_text())['inbounds'][1]['port'], 1024)
            self.assertEqual(json.loads(self.state_path.read_text())['xray']['port'], 1024)
        else:
            self.assert_unchanged()

    def test_missing_owned_tag_rejects_edit(self):
        self.runtime['inbounds'][1]['tag'] = 'user-customized-tag'
        self.save_fixture()
        result = self.run_edit('uuid', NEW_UUID + '\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()

    def test_protocol_mismatch_rejects_edit(self):
        self.runtime['inbounds'][1]['protocol'] = 'socks'
        self.save_fixture()
        result = self.run_edit('uuid', NEW_UUID + '\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()

    def test_validator_diagnostics_cannot_leak_private_keys(self):
        extra = f"""
xray() {{ printf 'config contains privateKey: {OLD_PRIVATE}\n' >&2; return 23; }}
"""
        result = self.run_edit('port', '8443\nYES\n', extra)
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        self.assertNotIn(OLD_PRIVATE, result.stdout + result.stderr)

    def test_generator_failure_or_invalid_output_never_changes_files(self):
        for output, status in [
            (f'PrivateKey: {NEW_PRIVATE}\nPassword: {NEW_PUBLIC}\n', 1),
            ('PrivateKey: invalid\nPassword: invalid\n', 0),
            ('', 0),
        ]:
            with self.subTest(status=status, output=output):
                extra = f"""
xray() {{ printf '%s' {shlex.quote(output)}; return {status}; }}
"""
                result = self.run_edit('reality-key', 'YES\n', extra)
                self.assertNotEqual(result.returncode, 0)
                self.assert_unchanged()
                self.assertNotIn(NEW_PRIVATE, result.stdout + result.stderr)

    def test_unsupported_field_profile_combinations_reject_without_changes(self):
        for profile, field, value in [('Trojan', 'uuid', NEW_UUID), ('mKCP', 'short-id', 'abcd'), ('mKCP', 'reality-key', None)]:
            with self.subTest(profile=profile, field=field):
                self.fixture(profile)
                result = self.run_edit(field, 'YES\n' if value is None else value + '\nYES\n')
                self.assertNotEqual(result.returncode, 0)
                self.assert_unchanged()
                self.assertNotIn('x25519', self.calls())

    def test_uuid_is_surgical_across_all_managed_vless_profiles(self):
        for profile in ['Vision', 'XHTTP', 'Fallback', 'mKCP', 'SNI']:
            with self.subTest(profile=profile):
                self.fixture(profile)
                result = self.run_edit('uuid', NEW_UUID + '\nYES\n')
                self.assert_success(result)
                state, runtime = copy.deepcopy(self.state), copy.deepcopy(self.runtime)
                state['xray']['uuid'] = NEW_UUID
                runtime['inbounds'][1]['settings']['clients'][0]['id'] = NEW_UUID
                self.assert_unchanged(state, runtime)

    def test_concurrent_change_while_confirming_is_preserved_and_aborts(self):
        extra = r"""
read() {
    builtin read "$@"
    local status=$?
    if [[ "${@: -1}" == answer && "$status" == 0 ]]; then
        jq '.concurrent_external_change = true' "$XRAY_CONFIG_PATH" > "$HOME/external.json"
        command mv "$HOME/external.json" "$XRAY_CONFIG_PATH"
    fi
    return "$status"
}
"""
        result = self.run_edit('port', '8443\nYES\n', extra)
        self.assertNotEqual(result.returncode, 0)
        expected = {**self.runtime, 'concurrent_external_change': True}
        self.assert_unchanged(original_runtime=expected)
        self.assertNotIn('restart', self.calls())

    def test_concurrent_change_during_validation_is_preserved_and_aborts(self):
        extra = r"""
xray() {
    jq '.concurrent_external_change = true' "$XRAY_CONFIG_PATH" > "$HOME/external.json"
    command mv "$HOME/external.json" "$XRAY_CONFIG_PATH"
}
"""
        result = self.run_edit('port', '8443\nYES\n', extra)
        self.assertNotEqual(result.returncode, 0)
        expected = {**self.runtime, 'concurrent_external_change': True}
        self.assert_unchanged(original_runtime=expected)
        self.assertNotIn('restart', self.calls())

    def test_concurrent_service_state_change_aborts_before_commit(self):
        extra = r"""
systemctl() {
    case " $* " in
    *' show '*)
        local count=0
        [[ ! -f "$HOME/show-count" ]] || count=$(cat "$HOME/show-count")
        count=$((count + 1)); printf '%s' "$count" > "$HOME/show-count"
        if [[ "$count" == 1 ]]; then echo ActiveState=active; else echo ActiveState=inactive; fi
        ;;
    *) printf 'unexpected systemctl %s\n' "$*" >> "$CALLS_FILE"; return 98 ;;
    esac
}
"""
        result = self.run_edit('port', '8443\nYES\n', extra)
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        self.assertNotIn('restart', self.calls())

    def test_existing_edit_lock_blocks_concurrent_writer(self):
        lock = Path(self.env['SCRIPT_CONFIG_DIR']) / 'config-edit.lock'
        with lock.open('w') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_edit('port', '8443\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        self.assertNotIn('restart', self.calls())

    def test_backup_root_symlink_is_rejected(self):
        target = self.home / 'unrelated-directory'
        target.mkdir()
        (Path(self.env['SCRIPT_CONFIG_DIR']) / 'backups').symlink_to(target, target_is_directory=True)
        result = self.run_edit('port', '8443\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        self.assertEqual(list(target.iterdir()), [])

    def test_runtime_symlink_is_rejected_without_modifying_target(self):
        target = self.home / 'actual-runtime.json'
        self.runtime_path.rename(target)
        self.runtime_path.symlink_to(target)
        result = self.run_edit('port', '8443\nYES\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        self.assertEqual(json.loads(target.read_text()), self.runtime)

    def test_interrupt_after_first_commit_restores_both_files(self):
        extra = r"""
eval "$(declare -f config_edit_replace | sed '1s/config_edit_replace/real_config_edit_replace/')"
config_edit_replace() {
    real_config_edit_replace "$@" || return $?
    if [[ ! -f "$HOME/interrupt-sent" ]]; then
        touch "$HOME/interrupt-sent"
        kill -TERM "$BASHPID"
    fi
}
"""
        result = self.run_edit('port', '8443\nYES\n', extra)
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        self.assertEqual((self.home / 'restart-count').read_text(), '1')

    def test_rollback_file_failure_is_visible_and_backup_remains(self):
        extra = r"""
eval "$(declare -f config_edit_replace | sed '1s/config_edit_replace/real_config_edit_replace/')"
config_edit_replace() {
    local count=0
    [[ ! -f "$HOME/write-count" ]] || count=$(cat "$HOME/write-count")
    count=$((count + 1)); printf '%s' "$count" > "$HOME/write-count"
    (( count < 2 )) || return 1
    real_config_edit_replace "$@"
}
"""
        result = self.run_edit('port', '8443\nYES\n', extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stdout + result.stderr, r'(?i)rollback[_ -]?(?:failed|failure)')
        backups = list((Path(self.env['SCRIPT_CONFIG_DIR']) / 'backups').glob('edit.*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads((backups[0] / 'runtime.json').read_text()), self.runtime)
        self.assertEqual(json.loads((backups[0] / 'script.json').read_text()), self.state)

    def test_failed_service_is_not_started(self):
        self.env['MOCK_SERVICE_STATE'] = 'failed'
        result = self.run_edit('port', '8443\nYES\n')
        self.assert_success(result)
        self.assertNotIn(' restart ', self.calls())
        self.assertNotIn(' start ', self.calls())

    def test_lowercase_yes_is_not_explicit_confirmation(self):
        result = self.run_edit('port', '8443\nyes\n')
        self.assert_success(result)
        self.assert_unchanged()
        self.assertFalse((Path(self.env['SCRIPT_CONFIG_DIR']) / 'backups').exists())

    def test_empty_key_rotation_confirmation_does_not_generate(self):
        result = self.run_edit('reality-key', '\n')
        self.assert_success(result)
        self.assert_unchanged()
        self.assertNotIn('x25519', self.calls())
        self.assertFalse((Path(self.env['SCRIPT_CONFIG_DIR']) / 'backups').exists())

    def test_success_guidance_shows_only_the_changed_client_values(self):
        cases = [
            ('uuid', NEW_UUID + '\nYES\n', 'client_uuid', [NEW_UUID]),
            ('reality-key', 'YES\n', 'client_reality_key', [NEW_PUBLIC]),
            ('short-id', '01,01234567\nYES\n', 'client_short_id', ['01', '01234567']),
            ('port', '8443\nYES\n', 'client_port', ['8443']),
        ]
        for field, stdin, label, values in cases:
            with self.subTest(field=field):
                self.fixture('Vision')
                result = self.run_edit(field, stdin)
                self.assert_success(result)
                output = result.stdout + result.stderr
                self.assertIn(label, output)
                self.assertIn('client_keep_others', output)
                self.assertIn('client_reconnect', output)
                for value in values:
                    self.assertIn(value, output)
                self.assertNotIn(OLD_PRIVATE, output)
                self.assertNotIn(NEW_PRIVATE, output)
                if field == 'port':
                    self.assertIn('client_port_firewall', output)

    def test_client_guidance_never_appears_on_cancel_error_or_rollback(self):
        for mode, stdin in [('cancel', '8443\nn\n'), ('invalid', '0\nYES\n'), ('validation', '8443\nYES\n'), ('rollback', '8443\nYES\n')]:
            with self.subTest(mode=mode):
                self.fixture('Vision')
                self.env['MOCK_TEST_EXIT'] = '23' if mode == 'validation' else '0'
                self.env['MOCK_RESTART_FAILURES'] = '1' if mode == 'rollback' else '0'
                result = self.run_edit('port', stdin)
                output = result.stdout + result.stderr
                self.assertNotRegex(output, r'client_(uuid|reality_key|short_id|port|keep_others|reconnect)')
                self.assertNotIn(NEW_PRIVATE, output)
                self.assert_unchanged()

    def test_client_sync_guidance_is_localized_in_chinese_and_english(self):
        for language in ['en', 'zh']:
            with self.subTest(language=language):
                self.fixture('Vision')
                path = ROOT / f'i18n/{language}.json'
                messages = json.loads(path.read_text())['config_edit']
                result = self.run_edit('port', '8443\nYES\n',
                                       'I18N_DATA=$(cat ' + shlex.quote(str(path)) + ')')
                self.assert_success(result)
                output = result.stdout + result.stderr
                for key in ['client_port', 'client_keep_others', 'client_reconnect', 'client_port_firewall']:
                    self.assertIn(messages[key], output)
                self.assertIn('8443', output)
                self.assertNotIn('null', output)

    def test_sensitive_values_are_not_passed_in_external_jq_argv(self):
        extra = r"""
jq() {
    printf '%s\n' "$@" >> "$HOME/jq-argv"
    command jq "$@"
}
"""
        for field, stdin in [('uuid', NEW_UUID + '\nYES\n'), ('reality-key', 'YES\n'), ('short-id', '01,01234567\nYES\n'), ('port', '8443\nYES\n')]:
            with self.subTest(field=field):
                self.fixture('Vision')
                result = self.run_edit(field, stdin, extra)
                self.assert_success(result)
                arguments = (self.home / 'jq-argv').read_text()
                for value in [OLD_PRIVATE, NEW_PRIVATE, OLD_UUID, NEW_UUID, OTHER_UUID, FALLBACK_UUID, '0123456789abcdef', '01234567']:
                    self.assertNotIn(value, arguments)

    def test_caller_xtrace_cannot_disclose_private_keys(self):
        result = self.run_edit('reality-key', 'YES\n', 'set -x')
        self.assert_success(result)
        output = result.stdout + result.stderr
        self.assertNotIn(OLD_PRIVATE, output)
        self.assertNotIn(NEW_PRIVATE, output)
        self.assertIn(NEW_PUBLIC, output)


class EditMenuTests(unittest.TestCase):
    def test_each_menu_entry_routes_to_one_independent_handler(self):
        function = named_function(ROOT / 'core/main.sh', 'processes_config')
        for number, handler in {4: '--change-port', 7: '--change-uuid', 8: '--change-reality-key', 9: '--change-short-id'}.items():
            with self.subTest(number=number):
                script = function + f"""
exec_menu() {{ return {number}; }}
exec_handler() {{ printf '%s' "$*"; }}
processes_config
"""
                result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, handler)

    def test_handler_flags_route_without_template_regeneration(self):
        functions = functions_from(ROOT / 'core/handler.sh')
        for flag, field in {
            '--change-port': 'port', '--change-uuid': 'uuid',
            '--change-reality-key': 'reality-key', '--change-short-id': 'short-id',
        }.items():
            with self.subTest(flag=flag):
                script = functions + f"""
load_i18n() {{ :; }}
handler_change_xray_field() {{ printf '%s' "$1"; }}
handler_xray_config() {{ echo 'UNEXPECTED_TEMPLATE_REGEN'; return 98; }}
handler_restart() {{ echo 'UNEXPECTED_RESTART'; return 98; }}
main {shlex.quote(flag)}
"""
                result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, field)

    def test_all_mutation_messages_are_localized_in_both_languages(self):
        source = (ROOT / 'core/config_edit.sh').read_text()
        keys = set(re.findall(r'config_edit_(?:message|fail) ([a-z_]+)', source))
        self.assertGreater(len(keys), 10)
        for language in ['en', 'zh']:
            messages = json.loads((ROOT / f'i18n/{language}.json').read_text())['config_edit']
            with self.subTest(language=language):
                self.assertTrue(keys <= messages.keys(), sorted(keys - messages.keys()))
                for key in keys:
                    self.assertIsInstance(messages[key], str)
                    self.assertTrue(messages[key].strip())

    def test_english_and_chinese_menu_render_all_nine_options(self):
        function = named_function(ROOT / 'core/menu.sh', 'menu_config')
        for language in ['en', 'zh']:
            with self.subTest(language=language):
                script = function + f"""
CUR_FILE=menu; GREEN=; NC=
I18N_DATA=$(cat {shlex.quote(str(ROOT / 'i18n' / (language + '.json')))})
menu_config
"""
                result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('null', result.stdout)
                for number in range(1, 10):
                    self.assertRegex(result.stdout, rf'(?m)^{number}\. .+')


class RealBinaryEditTests(ConfigEditCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get('XRAY_BINARY'):
            raise unittest.SkipTest('set XRAY_BINARY for real candidate validation')
        cls.binary = Path(os.environ['XRAY_BINARY']).resolve(strict=True)

    def test_each_edited_candidate_passes_real_xray_validation(self):
        self.env['XRAY_LOCATION_ASSET'] = str(self.binary.parent)
        extra = f"""
eval "$(declare -f xray | sed '1s/xray/mock_xray/')"
xray() {{
    if [[ "$1" == run ]]; then
        command {shlex.quote(str(self.binary))} "$@"
    else
        mock_xray "$@"
    fi
}}
"""
        for field, value in [('uuid', NEW_UUID + '\nYES\n'), ('short-id', '01234567\nYES\n'), ('port', '8443\nYES\n'), ('reality-key', 'YES\n')]:
            with self.subTest(field=field):
                self.fixture('Vision')
                result = self.run_edit(field, value, extra)
                self.assert_success(result)


if __name__ == '__main__':
    unittest.main()
