#!/usr/bin/env python3
"""Hermetic CentOS 7 core-only upgrade tests: no host installer or services.

Temporary executable fixtures only emulate `version` and `run -test`. Official
asset downloads and every systemd/account operation are mocked. No network is
used, and no real Xray service or host configuration is touched.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import unittest
import warnings
import zipfile

from test_regressions import ROOT, ShellCase, functions_from, named_function


class LegacyUpgradeCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='xray-legacy-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.binary = self.home / 'usr/local/bin/xray'
        self.binary.parent.mkdir(parents=True)
        self.runtime = self.home / 'usr/local/etc/xray/config.json'
        self.runtime.parent.mkdir(parents=True)
        self.runtime.write_text(json.dumps({'inbounds': [], 'outbounds': [], 'fixture': 'preserve-runtime'}))
        self.runtime.chmod(0o640)
        self.state_dir = self.home / 'state'
        self.state_dir.mkdir()
        self.state_path = self.state_dir / 'config.json'
        self.state_path.write_text(json.dumps({'xray': {'version': 'v25.3.6'}, 'fixture': 'preserve-state'}))
        self.state_path.chmod(0o600)
        self.unit = self.home / 'etc/systemd/system/xray.service'
        self.unit.parent.mkdir(parents=True)
        self.unit.write_text('[Service]\nUser=root\nExecStart=' + str(self.binary) + ' run -config ' + str(self.runtime) + '\n')
        self.geodata = self.home / 'usr/local/share/xray'
        self.geodata.mkdir(parents=True)
        (self.geodata / 'geoip.dat').write_bytes(b'custom geoip fixture')
        (self.geodata / 'geosite.dat').write_bytes(b'custom geosite fixture')
        self.calls_file = self.home / 'calls'
        self.old_binary = self.binary_fixture('25.3.6', 'old')
        self.binary.write_bytes(self.old_binary)
        self.binary.chmod(0o755)
        self.calls_file = self.home / 'calls'
        self.env = os.environ.copy()
        self.env.update(
            HOME=str(self.home), TMPDIR=str(self.home),
            SCRIPT_CONFIG_DIR=str(self.state_dir), SCRIPT_CONFIG_PATH=str(self.state_path),
            XRAY_CONFIG_PATH=str(self.runtime), XRAY_BINARY_PATH=str(self.binary),
            CALLS_FILE=str(self.calls_file), DAT_PATH=str(self.geodata),
            MOCK_MACHINE='x86_64', MOCK_SERVICE_STATE='active',
            MOCK_CURL_FAILURE='0', MOCK_NEW_VERSION_EXIT='0', MOCK_NEW_TEST_EXIT='0',
            MOCK_START_FAILURES='0', MOCK_RESTART_FAILURES='0',
        )
        self.create_archive()

    def binary_fixture(self, version, identity):
        return f'''#!/usr/bin/env bash
printf 'binary {identity} %s\\n' "$*" >> {shlex.quote(str(self.calls_file))}
case "$1" in
version)
    printf 'Xray {version} (public test fixture)\\n'
    [[ {shlex.quote(identity)} != new ]] || exit "$(cat {shlex.quote(str(self.home / 'new-version-exit'))})"
    ;;
run)
    [[ " $* " == *' -test '* ]] || exit 98
    [[ {shlex.quote(identity)} != new ]] || exit "$(cat {shlex.quote(str(self.home / 'new-test-exit'))})"
    ;;
*) exit 98 ;;
esac
'''.encode()

    def create_archive(self, *, binary=None, extra_members=None):
        self.archive = self.home / 'Xray-linux-64.zip'
        with zipfile.ZipFile(self.archive, 'w') as archive:
            member = zipfile.ZipInfo('xray')
            member.create_system = 3
            member.external_attr = (stat.S_IFREG | 0o755) << 16
            archive.writestr(member, self.binary_fixture('26.10.21', 'new') if binary is None else binary)
            archive.writestr('geoip.dat', b'new geoip must never replace existing data')
            archive.writestr('geosite.dat', b'new geosite must never replace existing data')
            for name, data in (extra_members or {}).items():
                archive.writestr(name, data)
        self.digest = self.home / 'Xray-linux-64.zip.dgst'
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.digest.write_text('SHA256= ' + digest + '\n')
        self.env['MOCK_ARCHIVE'] = str(self.archive)
        self.env['MOCK_DIGEST'] = str(self.digest)

    def run_upgrade(self, version='v26.10.21', extra=''):
        # The module's path contract is bound below once loaded. Every location
        # it can mutate points inside this TemporaryDirectory.
        source = 'source ' + shlex.quote(str(ROOT / 'core/platform.sh')) + '\nsource ' + shlex.quote(str(ROOT / 'core/legacy_upgrade.sh'))
        (self.home / 'new-version-exit').write_text(self.env['MOCK_NEW_VERSION_EXIT'])
        (self.home / 'new-test-exit').write_text(self.env['MOCK_NEW_TEST_EXIT'])
        harness = r"""
SCRIPT_CONFIG=$(cat "$SCRIPT_CONFIG_PATH")
legacy_binary_path() { printf '%s\n' "$XRAY_BINARY_PATH"; }
I18N_DATA='{}'; GREEN=; RED=; YELLOW=; NC=
_error() { printf '%s\n' "$*" >&2; exit 1; }
sleep() { :; }
uname() {
    case "$1" in -m) printf '%s\n' "$MOCK_MACHINE" ;; *) command uname "$@" ;; esac
}
curl() {
    local url='' output=''
    printf 'curl %s\n' "$*" >> "$CALLS_FILE"
    while [[ $# -gt 0 ]]; do
        case "$1" in
        -o|--output) output=$2; shift ;;
        https://*) url=$1 ;;
        esac
        shift
    done
    [[ "$MOCK_CURL_FAILURE" == 0 ]] || return "$MOCK_CURL_FAILURE"
    [[ "$output" == "$HOME/"* ]] || return 97
    case "$url" in
    */Xray-linux-64.zip.dgst) cp "$MOCK_DIGEST" "$output" ;;
    */Xray-linux-64.zip) cp "$MOCK_ARCHIVE" "$output" ;;
    *) echo 'unexpected download URL' >&2; return 98 ;;
    esac
}
systemctl() {
    printf 'systemctl %s\n' "$*" >> "$CALLS_FILE"
    local property='' previous='' argument state="$MOCK_SERVICE_STATE" action count=0
    [[ ! -f "$HOME/service-state" ]] || state=$(cat "$HOME/service-state")
    case " $* " in
    *' show '*)
        if [[ "$*" == 'show -p LoadState -p RootImage xray --no-pager --full' ]]; then
            printf 'LoadState=loaded\n'
            return 0
        fi
        # CentOS 7 builds can reject a singleton unknown property. Do not
        # model that as a successful empty response: this hid the real bug.
        if [[ "$1 $2 $3" == 'show -p RootImage' ]]; then
            printf 'unknown property\n' >&2
            return 1
        fi
        for argument in "$@"; do
            if [[ "$previous" == -p || "$previous" == --property ]]; then property=$argument; fi
            case "$argument" in --property=*) property=${argument#*=} ;; esac
            previous=$argument
        done
        case "$property" in
        ActiveState) printf 'ActiveState=%s\n' "$state" ;;
        LoadState) printf 'LoadState=loaded\n' ;;
        ExecStart) printf 'ExecStart={ path=%s ; argv[]=%s run -config %s ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }\n' "$XRAY_BINARY_PATH" "$XRAY_BINARY_PATH" "$XRAY_CONFIG_PATH" ;;
        FragmentPath) printf 'FragmentPath=%s\n' "$HOME/etc/systemd/system/xray.service" ;;
        User) printf 'User=root\n' ;;
        Environment) printf 'Environment=XRAY_LOCATION_ASSET=%s\n' "$DAT_PATH" ;;
        Group|EnvironmentFiles|WorkingDirectory|RootDirectory|RootImage|ExecStartPre|ExecStartPost) printf '%s=\n' "$property" ;;
        *) echo "unexpected property: $property" >&2; return 98 ;;
        esac
        ;;
    *' cat '*) cat "$HOME/etc/systemd/system/xray.service" ;;
    *' is-active '*) [[ "$state" == active ]] ;;
    *' stop '*) printf 'inactive' > "$HOME/service-state" ;;
    *' start '*|*' restart '*)
        action=start
        [[ " $* " != *' restart '* ]] || action=restart
        [[ ! -f "$HOME/$action-count" ]] || count=$(cat "$HOME/$action-count")
        count=$((count + 1)); printf '%s' "$count" > "$HOME/$action-count"
        if { [[ "$action" == start ]] && (( count <= MOCK_START_FAILURES )); } ||
           { [[ "$action" == restart ]] && (( count <= MOCK_RESTART_FAILURES )); }; then
            printf 'failed' > "$HOME/service-state"; return 1
        fi
        printf 'active' > "$HOME/service-state"
        ;;
    *) echo 'unexpected systemctl mutation' >&2; return 98 ;;
    esac
}
systemd-sysusers() { echo 'ACCOUNT_MUTATION systemd-sysusers' >> "$CALLS_FILE"; return 98; }
useradd() { echo 'ACCOUNT_MUTATION useradd' >> "$CALLS_FILE"; return 98; }
groupadd() { echo 'ACCOUNT_MUTATION groupadd' >> "$CALLS_FILE"; return 98; }
"""
        return subprocess.run(['bash', '--noprofile', '--norc', '-c', source + '\n' + harness + '\n' + extra + '\nlegacy_upgrade_xray ' + shlex.quote(version)],
                              env=self.env, cwd=self.home, capture_output=True, text=True, timeout=20)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def override_property(self, property_name, value, *, omit=False):
        if property_name == 'RootImage':
            response = 'LoadState=loaded\n' + ('' if omit else 'RootImage=' + value + '\n')
            return r"""
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    if [[ "$*" == 'show -p LoadState -p RootImage xray --no-pager --full' ]]; then
        printf '%s' """ + shlex.quote(response) + r"""
        return 0
    fi
    fixture_systemctl "$@"
}
"""
        return r"""
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
""" + f"""
    if [[ "$1" == show && "$2" == -p && "$3" == {shlex.quote(property_name)} ]]; then
        printf '%s' {shlex.quote('' if omit else property_name + '=' + value + chr(10))}
        return 0
    fi
""" + r"""
    fixture_systemctl "$@"
}
"""

    def refresh_digest(self):
        self.digest.write_text('SHA256= ' + hashlib.sha256(self.archive.read_bytes()).hexdigest() + '\n')

    def calls(self):
        return self.calls_file.read_text() if self.calls_file.exists() else ''

    def assert_preserved_data(self, version='v25.3.6'):
        self.assertEqual(json.loads(self.runtime.read_text()), {'inbounds': [], 'outbounds': [], 'fixture': 'preserve-runtime'})
        self.assertEqual(json.loads(self.state_path.read_text()), {'xray': {'version': version}, 'fixture': 'preserve-state'})
        self.assertEqual((self.geodata / 'geoip.dat').read_bytes(), b'custom geoip fixture')
        self.assertEqual((self.geodata / 'geosite.dat').read_bytes(), b'custom geosite fixture')
        self.assertIn('User=root\n', self.unit.read_text())

    def assert_no_service_mutation(self):
        calls = self.calls()
        for action in [' stop ', ' start ', ' restart ', ' daemon-reload', ' enable ', ' disable ']:
            self.assertNotIn('systemctl' + action, calls)
        self.assertNotIn('ACCOUNT_MUTATION', calls)


class LegacyUpgradeTests(LegacyUpgradeCase):
    def test_active_upgrade_validates_before_stop_and_preserves_user_files(self):
        result = self.run_upgrade()
        self.assert_success(result)
        self.assertEqual(self.binary.read_bytes(), self.binary_fixture('26.10.21', 'new'))
        self.assert_preserved_data(version='v26.10.21')
        calls = self.calls()
        self.assertLess(calls.index('binary new run'), calls.index('systemctl stop'))
        self.assertIn('https://github.com/XTLS/Xray-core/releases/download/v26.10.21/Xray-linux-64.zip', calls)
        self.assertNotIn('Xray-install', calls)
        self.assertNotIn('ACCOUNT_MUTATION', calls)
        self.assertEqual(self.binary.stat().st_mode & 0o777, 0o755)

    def test_inactive_service_remains_inactive_after_upgrade(self):
        self.env['MOCK_SERVICE_STATE'] = 'inactive'
        result = self.run_upgrade()
        self.assert_success(result)
        self.assertEqual(self.binary.read_bytes(), self.binary_fixture('26.10.21', 'new'))
        self.assert_no_service_mutation()
        self.assert_preserved_data(version='v26.10.21')

    def test_failed_service_is_not_automatically_started(self):
        self.env['MOCK_SERVICE_STATE'] = 'failed'
        result = self.run_upgrade()
        self.assert_success(result)
        self.assert_no_service_mutation()
        self.assert_preserved_data(version='v26.10.21')

    def test_download_failure_does_not_stop_old_service(self):
        self.env['MOCK_CURL_FAILURE'] = '22'
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assert_no_service_mutation()
        self.assert_preserved_data()

    def test_sha256_mismatch_rejects_archive_before_service_stop(self):
        self.digest.write_text('SHA256= ' + ('0' * 64) + '\n')
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assert_no_service_mutation()

    def test_empty_or_malformed_digest_fails_closed(self):
        for digest in ['', 'SHA256= invalid\n', 'not found\n', 'SHA256= ' + ('a' * 63) + '\n']:
            with self.subTest(digest=digest):
                self.digest.write_text(digest)
                result = self.run_upgrade()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.binary.read_bytes(), self.old_binary)
                self.assert_no_service_mutation()

    def test_candidate_wrong_version_does_not_stop_old_service(self):
        self.create_archive(binary=self.binary_fixture('26.9.8', 'new'))
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assert_no_service_mutation()

    def test_candidate_version_command_nonzero_is_not_success(self):
        self.env['MOCK_NEW_VERSION_EXIT'] = '1'
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assert_no_service_mutation()

    def test_candidate_config_validation_failure_happens_before_stop(self):
        self.env['MOCK_NEW_TEST_EXIT'] = '23'
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assert_no_service_mutation()
        self.assert_preserved_data()

    def test_transitional_service_state_is_rejected_before_mutation(self):
        self.env['MOCK_SERVICE_STATE'] = 'activating'
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assert_no_service_mutation()

    def test_absent_existing_core_is_not_a_fresh_install(self):
        self.binary.unlink()
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.binary.exists())
        self.assert_no_service_mutation()
        self.assertNotIn('curl ', self.calls())

    def test_invalid_requested_version_cannot_inject_commands(self):
        marker = self.home / 'injected'
        for version in ['', 'latest', 'v26.10.21/../../main', f'$(touch {marker})']:
            with self.subTest(version=version):
                result = self.run_upgrade(version)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists())
                self.assertEqual(self.binary.read_bytes(), self.old_binary)
                self.assert_no_service_mutation()

    def test_unsupported_architecture_fails_without_stopping_service(self):
        self.env['MOCK_MACHINE'] = 'unsupported-fixture-arch'
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assert_no_service_mutation()

    def test_new_binary_start_failure_rolls_back_old_binary(self):
        self.env['MOCK_START_FAILURES'] = '1'
        self.env['MOCK_RESTART_FAILURES'] = '1'
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertEqual((self.home / 'service-state').read_text(), 'active')
        self.assert_preserved_data()

    def test_rollback_restart_failure_is_reported(self):
        self.env['MOCK_START_FAILURES'] = '5'
        self.env['MOCK_RESTART_FAILURES'] = '5'
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertRegex(result.stdout + result.stderr, r'(?i)(rollback|restore|recovery).*(fail|manual|incomplete)|manual.*(recover|restore)')
        self.assert_preserved_data()


    def test_systemd_219_missing_root_image_property_is_supported(self):
        result = self.run_upgrade(extra=self.override_property('RootImage', '', omit=True))
        self.assert_success(result)
        self.assert_preserved_data(version='v26.10.21')

    def test_nonstandard_service_commands_are_rejected_before_download(self):
        for command in [
            f'{self.binary} run -confdir {self.runtime.parent}',
            f'{self.binary} run -config {self.runtime} -config /unrelated.json',
            f'/usr/local/bin/wrapper run -config {self.runtime}',
        ]:
            with self.subTest(command=command):
                serialized = f'{{ path={self.binary} ; argv[]={command} ; ignore_errors=no ; }}'
                result = self.run_upgrade(extra=self.override_property('ExecStart', serialized))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.binary.read_bytes(), self.old_binary)
                self.assert_no_service_mutation()
                self.assertNotIn('curl ', self.calls())

    def test_unsupported_unit_environment_hooks_and_accounts_are_rejected(self):
        for name, value in [
            ('EnvironmentFiles', '/etc/default/xray'),
            ('Environment', 'XRAY_LOCATION_CONFIG=/custom'),
            ('RootDirectory', '/chroot'), ('RootImage', '/custom.raw'),
            ('WorkingDirectory', '/custom'), ('ExecStartPre', '/custom/hook'),
            ('ExecStartPost', '/custom/hook'), ('User', 'nobody'), ('Group', 'wheel'),
        ]:
            with self.subTest(property=name, value=value):
                result = self.run_upgrade(extra=self.override_property(name, value))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.binary.read_bytes(), self.old_binary)
                self.assert_no_service_mutation()

    def test_path_traversal_archive_is_rejected_without_extraction(self):
        self.create_archive(extra_members={'../unexpected-outside': b'must not extract'})
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_service_mutation()
        self.assertFalse((self.home / 'unexpected-outside').exists())
        self.assertEqual(self.binary.read_bytes(), self.old_binary)

    def test_duplicate_binary_archive_member_is_rejected(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.archive, 'a') as archive:
                archive.writestr('xray', self.binary_fixture('26.10.21', 'new'))
        self.refresh_digest()
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_service_mutation()

    def test_archive_without_binary_is_rejected(self):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr('README.md', b'missing binary')
        self.refresh_digest()
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_service_mutation()

    def test_symlink_binary_member_is_rejected(self):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            member = zipfile.ZipInfo('xray')
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(member, '/unrelated/binary')
        self.refresh_digest()
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_service_mutation()

    def test_atomic_replace_failure_restores_original_and_original_active_state(self):
        extra = r"""
eval "$(declare -f legacy_binary_replace | sed '1s/legacy_binary_replace/real_legacy_binary_replace/')"
legacy_binary_replace() {
    if [[ ! -f "$HOME/replace-failed" ]]; then touch "$HOME/replace-failed"; return 1; fi
    real_legacy_binary_replace "$@"
}
"""
        result = self.run_upgrade(extra=extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertEqual((self.home / 'service-state').read_text(), 'active')
        self.assert_preserved_data()

    def test_partial_stop_failure_recovers_unchanged_old_service(self):
        extra = r"""
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    if [[ "$1" == stop ]]; then
        printf 'inactive' > "$HOME/service-state"
        return 1
    fi
    fixture_systemctl "$@"
}
"""
        result = self.run_upgrade(extra=extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertEqual((self.home / 'service-state').read_text(), 'active')
        self.assert_preserved_data()

    def test_existing_config_edit_lock_prevents_core_upgrade(self):
        with (self.state_dir / 'config-edit.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assert_no_service_mutation()
        self.assertNotIn('curl ', self.calls())

    def test_private_backup_preserves_original_binary_and_mode(self):
        result = self.run_upgrade()
        self.assert_success(result)
        backups = list((self.state_dir / 'backups').glob('core.*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o700)
        self.assertEqual((backups[0] / 'binary.before').read_bytes(), self.old_binary)
        self.assertEqual((backups[0] / 'binary.before').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.binary.stat().st_mode & 0o777, 0o755)

    def test_binary_user_xattrs_are_preserved(self):
        try:
            os.setxattr(self.binary, b'user.xray-test', b'preserve-xattr')
        except OSError as error:
            self.skipTest(f'filesystem does not support user xattrs: {error}')
        result = self.run_upgrade()
        self.assert_success(result)
        self.assertEqual(os.getxattr(self.binary, b'user.xray-test'), b'preserve-xattr')

    def test_existing_binary_symlink_is_rejected(self):
        target = self.binary.with_name('actual-xray')
        self.binary.rename(target)
        self.binary.symlink_to(target)
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_bytes(), self.old_binary)
        self.assert_no_service_mutation()

    def test_validation_diagnostics_are_not_disclosed(self):
        marker = 'PUBLIC-FIXTURE-SECRET-LIKE-DIAGNOSTIC'
        candidate = self.binary_fixture('26.10.21', 'new').replace(b'run)\n', ('run)\necho ' + marker + ' >&2\n').encode())
        self.create_archive(binary=candidate)
        self.env['MOCK_NEW_TEST_EXIT'] = '23'
        result = self.run_upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(marker, result.stdout + result.stderr)
        self.assert_no_service_mutation()


    def test_lingering_failed_new_process_is_stopped_before_restore(self):
        extra = r"""
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    if [[ "$1" == start && ! -f "$HOME/first-start-failed" ]]; then
        touch "$HOME/first-start-failed"
        printf 'active' > "$HOME/service-state"
        echo 'systemctl start xray (failed leaving active)' >> "$CALLS_FILE"
        return 1
    fi
    fixture_systemctl "$@"
}
"""
        result = self.run_upgrade(extra=extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertEqual((self.home / 'service-state').read_text(), 'active')
        self.assertEqual(self.calls().count('systemctl stop xray'), 2)

    def test_interrupted_first_swap_rolls_back_before_restarting_original(self):
        extra = r"""
eval "$(declare -f legacy_binary_replace | sed '1s/legacy_binary_replace/real_legacy_binary_replace/')"
legacy_binary_replace() {
    real_legacy_binary_replace "$@" || return $?
    if [[ ! -f "$HOME/interrupt-sent" ]]; then
        touch "$HOME/interrupt-sent"
        kill -TERM "$BASHPID"
    fi
}
"""
        result = self.run_upgrade(extra=extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertEqual((self.home / 'service-state').read_text(), 'active')
        self.assert_preserved_data()

    def test_candidate_validation_preserves_concurrent_config_changes(self):
        extra = r"""
eval "$(declare -f legacy_test_candidate | sed '1s/legacy_test_candidate/real_legacy_test_candidate/')"
legacy_test_candidate() {
    real_legacy_test_candidate "$@" || return $?
    printf '%s' '{"external":"changed-during-validation"}' > "$XRAY_CONFIG_PATH"
}
"""
        result = self.run_upgrade(extra=extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertEqual(json.loads(self.runtime.read_text()), {'external': 'changed-during-validation'})
        self.assert_no_service_mutation()

    def test_candidate_validation_preserves_concurrent_asset_changes(self):
        extra = r"""
eval "$(declare -f legacy_test_candidate | sed '1s/legacy_test_candidate/real_legacy_test_candidate/')"
legacy_test_candidate() {
    real_legacy_test_candidate "$@" || return $?
    printf 'external-new-geoip' > "$DAT_PATH/geoip.dat"
}
"""
        result = self.run_upgrade(extra=extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertEqual((self.geodata / 'geoip.dat').read_bytes(), b'external-new-geoip')
        self.assert_no_service_mutation()

    def test_unit_change_during_validation_aborts_before_stop(self):
        extra = r"""
eval "$(declare -f legacy_test_candidate | sed '1s/legacy_test_candidate/real_legacy_test_candidate/')"
legacy_test_candidate() {
    real_legacy_test_candidate "$@" || return $?
    echo '# external change' >> "$HOME/etc/systemd/system/xray.service"
}
"""
        result = self.run_upgrade(extra=extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertIn('# external change', self.unit.read_text())
        self.assert_no_service_mutation()


    def test_systemd_219_empty_optional_arrays_are_supported(self):
        extra = r"""
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    if [[ "$1" == show && "$2" == -p ]]; then
        case "$3" in EnvironmentFiles|RootImage|ExecStartPre|ExecStartPost) return 0 ;; esac
    fi
    fixture_systemctl "$@"
}
"""
        result = self.run_upgrade(extra=extra)
        self.assert_success(result)
        self.assert_preserved_data(version='v26.10.21')

    def test_failed_optional_property_query_is_not_silently_ignored(self):
        extra = r"""
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    [[ "$*" != 'show -p LoadState -p RootImage xray --no-pager --full' ]] || return 1
    fixture_systemctl "$@"
}
"""
        result = self.run_upgrade(extra=extra)
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_service_mutation()
        self.assertEqual(self.binary.read_bytes(), self.old_binary)

    def test_failed_capability_inspection_aborts_before_service_stop(self):
        result = self.run_upgrade(extra='legacy_capabilities() { return 1; }')
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_service_mutation()
        self.assertEqual(self.binary.read_bytes(), self.old_binary)

    def test_interrupt_after_candidate_started_stops_it_before_restoring(self):
        extra = r"""
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    fixture_systemctl "$@" || return $?
    if [[ "$1" == start && ! -f "$HOME/start-interrupt-sent" ]]; then
        touch "$HOME/start-interrupt-sent"
        kill -TERM "$BASHPID"
    fi
}
"""
        result = self.run_upgrade(extra=extra)
        self.assertEqual(result.returncode, 143, result.stdout + result.stderr)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertEqual((self.home / 'service-state').read_text(), 'active')
        self.assertEqual(self.calls().count('systemctl stop xray'), 2)
        self.assert_preserved_data()

    def test_version_metadata_failure_rolls_back_binary_and_state(self):
        extra = r"""
eval "$(declare -f legacy_replace_state | sed '1s/legacy_replace_state/real_legacy_replace_state/')"
legacy_replace_state() {
    if [[ ! -f "$HOME/state-write-failed" ]]; then touch "$HOME/state-write-failed"; return 1; fi
    real_legacy_replace_state "$@"
}
"""
        result = self.run_upgrade(extra=extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertEqual((self.home / 'service-state').read_text(), 'active')
        self.assert_preserved_data()

    def test_concurrent_script_state_change_is_preserved_without_stopping_service(self):
        extra = r"""
eval "$(declare -f legacy_test_candidate | sed '1s/legacy_test_candidate/real_legacy_test_candidate/')"
legacy_test_candidate() {
    real_legacy_test_candidate "$@" || return $?
    jq '.external_change=true' "$SCRIPT_CONFIG_PATH" > "$HOME/external-state"
    mv "$HOME/external-state" "$SCRIPT_CONFIG_PATH"
}
"""
        result = self.run_upgrade(extra=extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertTrue(json.loads(self.state_path.read_text())['external_change'])
        self.assertEqual(json.loads(self.state_path.read_text())['xray']['version'], 'v25.3.6')
        self.assert_no_service_mutation()

    def test_changed_loaded_service_properties_abort_even_when_unit_file_is_same(self):
        extra = r"""
eval "$(declare -f legacy_test_candidate | sed '1s/legacy_test_candidate/real_legacy_test_candidate/')"
legacy_test_candidate() {
    real_legacy_test_candidate "$@" || return $?
    touch "$HOME/change-loaded-properties"
}
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    if [[ "$1 $2 $3" == 'show -p Group' && -f "$HOME/change-loaded-properties" ]]; then
        echo 'Group=root'; return 0
    fi
    fixture_systemctl "$@"
}
"""
        result = self.run_upgrade(extra=extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assert_no_service_mutation()



class SysusersPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='xray-platform-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.candidate = self.home / 'usr/lib/systemd/systemd-sysusers'
        self.candidate.parent.mkdir(parents=True)

    def preflight(self, extra=''):
        script = 'source ' + shlex.quote(str(ROOT / 'core/platform.sh')) + f"""
xray_sysusers_candidates() {{ printf '%s\n' {shlex.quote(str(self.candidate))}; }}
systemctl() {{ echo UNEXPECTED_SERVICE_MUTATION >&2; return 98; }}
systemd-sysusers() {{ echo UNEXPECTED_ACCOUNT_FUNCTION >&2; return 98; }}
""" + extra + '\nxray_sysusers_preflight'
        return subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                              env={**os.environ, 'HOME': str(self.home)}, cwd=self.home,
                              capture_output=True, text=True, timeout=10)

    def test_missing_real_binary_fails_even_with_shell_function_present(self):
        result = self.preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('UNEXPECTED_SERVICE_MUTATION', result.stdout + result.stderr)
        self.assertNotIn('UNEXPECTED_ACCOUNT_FUNCTION', result.stdout + result.stderr)

    def test_non_executable_binary_is_rejected(self):
        self.candidate.write_text('#!/usr/bin/env bash\nexit 0\n')
        self.candidate.chmod(0o644)
        result = self.preflight()
        self.assertNotEqual(result.returncode, 0)

    def test_broken_version_command_is_rejected(self):
        self.candidate.write_text('#!/usr/bin/env bash\necho systemd 252\nexit 1\n')
        self.candidate.chmod(0o755)
        result = self.preflight()
        self.assertNotEqual(result.returncode, 0)

    def test_real_non_path_binary_is_checked_without_provisioning_accounts(self):
        log = self.home / 'calls'
        self.candidate.write_text('#!/usr/bin/env bash\nprintf "%s\n" "$*" >> ' + shlex.quote(str(log)) + '\n[[ "$*" == --version ]] || exit 98\nprintf "systemd 252\n"\n')
        self.candidate.chmod(0o755)
        result = self.preflight()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(log.read_text().splitlines(), ['--version'])
        self.assertNotIn('UNEXPECTED_ACCOUNT_FUNCTION', result.stdout + result.stderr)


class CentosRoutingTests(ShellCase):
    def test_forced_centos7_update_uses_legacy_core_only_path(self):
        result = self.run_shell('handler_install v26.10.21 y', prelude=r"""
xray_install_platform() { echo centos:7; }
cmd_exists() { return 0; }
legacy_upgrade_xray() {
    printf 'legacy %s\n' "$*" > "$CALLS_FILE"
    jq --arg version "$1" '.xray.version=$version | .concurrent_new="preserve"' "$SCRIPT_CONFIG_PATH" > "$HOME/fresh-state"
    mv "$HOME/fresh-state" "$SCRIPT_CONFIG_PATH"
}
legacy_binary_path() { echo xray; }
run_xray_installer() { echo official-installer >> "$CALLS_FILE"; return 98; }
xray() { echo 'Xray 26.10.21 (public fixture)'; }
""")
        self.assert_success(result)
        self.assertEqual((self.home / 'calls').read_text().strip(), 'legacy v26.10.21')
        self.assertEqual(json.loads(self.config_file.read_text())['xray']['version'], 'v26.10.21')
        self.assertEqual(json.loads(self.config_file.read_text())['concurrent_new'], 'preserve')

    def test_centos7_fresh_install_cannot_enter_legacy_or_official_installer(self):
        before = self.config_file.read_bytes()
        result = self.run_shell('handler_install v26.10.21 n', prelude=r"""
xray_install_platform() { echo centos:7; }
cmd_exists() { return 1; }
legacy_upgrade_xray() { echo legacy > "$CALLS_FILE"; return 98; }
run_xray_installer() { echo official-installer > "$CALLS_FILE"; return 98; }
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config_file.read_bytes(), before)
        self.assertFalse((self.home / 'calls').exists())

    def test_centos7_quick_install_is_rejected_before_reset_or_key_generation(self):
        before = self.config_file.read_bytes()
        result = self.run_shell('handler_quick_install Vision', prelude=r"""
xray_install_platform() { echo centos:7; }
handler_script_config() { echo RESET > "$CALLS_FILE"; }
handler_install() { echo INSTALL >> "$CALLS_FILE"; }
handler_x25519_config() { echo KEYS >> "$CALLS_FILE"; }
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config_file.read_bytes(), before)
        self.assertFalse((self.home / 'calls').exists())

    def test_centos7_full_config_dispatch_is_rejected_before_mutation(self):
        before = self.config_file.read_bytes()
        for option in ['--script-config', '--xray-config']:
            with self.subTest(option=option):
                result = self.run_shell('main ' + option + ' Vision', prelude=r"""
xray_install_platform() { echo centos:7; }
load_i18n() { :; }
handler_read_xray_config() { echo READ > "$CALLS_FILE"; }
handler_script_config() { echo RESET > "$CALLS_FILE"; }
handler_sni_config() { echo SNI > "$CALLS_FILE"; }
handler_x25519_config() { echo KEYS > "$CALLS_FILE"; }
handler_xray_config() { echo CONFIG > "$CALLS_FILE"; }
""")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.config_file.read_bytes(), before)
                self.assertFalse((self.home / 'calls').exists())

    def test_modern_missing_sysusers_preflight_prevents_download_and_installer(self):
        result = self.run_shell('run_xray_installer install --version v26.10.21', prelude=r"""
xray_sysusers_preflight() { return 1; }
curl() { echo DOWNLOAD > "$CALLS_FILE"; return 98; }
bash() { echo EXECUTE > "$CALLS_FILE"; return 98; }
systemctl() { echo SERVICE > "$CALLS_FILE"; return 98; }
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / 'calls').exists())

    def test_full_install_menu_checks_platform_before_any_menu_or_version_write(self):
        for function in ['processes_full_installation', 'processes_xray_config']:
            with self.subTest(function=function):
                result = self.run_shell(function, file='core/main.sh', prelude=r"""
HANDLER_PATH="$HOME/mock-handler"
bash() { printf '%s\n' "$*" >> "$CALLS_FILE"; return 1; }
exec_menu() { echo MENU >> "$CALLS_FILE"; return 1; }
""")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.home / 'calls').read_text().splitlines(),
                                 [str(self.home / 'mock-handler') + ' --check-legacy-install'])
                (self.home / 'calls').unlink()


class PortablePlatformTests(unittest.TestCase):
    def test_os_release_is_parsed_without_evaluating_shell_code(self):
        with tempfile.TemporaryDirectory(prefix='xray-os-fixture-') as temporary:
            directory = Path(temporary)
            release = directory / 'os-release'
            marker = directory / 'executed'
            release.write_text('ID="centos"\nVERSION_ID="7.9"\nUNTRUSTED=$(touch ' + str(marker) + ')\n')
            script = 'source ' + shlex.quote(str(ROOT / 'core/platform.sh')) + '\nxray_install_platform ' + shlex.quote(str(release))
            result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'centos:7')
            self.assertFalse(marker.exists())

    def test_systemd_219_property_access_does_not_use_show_value(self):
        script = 'source ' + shlex.quote(str(ROOT / 'core/platform.sh')) + r"""
systemctl() { [[ "$*" == 'show -p ActiveState xray --no-pager --full' ]] || return 98; echo 'ActiveState=active'; }
xray_service_property ActiveState
"""
        result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'active')

    def test_missing_critical_property_is_not_treated_as_inactive(self):
        script = 'source ' + shlex.quote(str(ROOT / 'core/platform.sh')) + r"""
systemctl() { return 0; }
xray_service_property ActiveState
"""
        result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
