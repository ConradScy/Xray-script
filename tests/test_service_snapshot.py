#!/usr/bin/env python3
"""Safe regressions for full systemd 219 service-property snapshots.

No real systemctl, download, installer, service or account mutation is executed.
The full-script fixture includes real globals, main entry points and a handler
child process. Only the upgrade boundary is replaced by a read-only snapshot.
"""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from test_regressions import ROOT
import test_legacy_upgrade as legacy_tests

FULL_SHOW = '--no-pager --full --all show xray'
SECRET = 'fixture-sensitive-marker-never-print'
USER_EXEC_START = (
    'ExecStart={ path=/usr/local/bin/xray ; '
    'argv[]=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json ; '
    'ignore_errors=no ; start_time=[四 2026-10-29 18:01:53 CST] ; '
    'stop_time=[四 2026-10-29 18:25:02 CST] ; pid=4674 ; code=exited ; status=0 }'
)
REQUIRED = ['Id', 'LoadState', 'ActiveState', 'ExecStart', 'User', 'Group',
            'WorkingDirectory', 'RootDirectory', 'Environment']
OPTIONAL = ['RootImage', 'ExecStartPre', 'ExecStartPost', 'EnvironmentFiles']


class FullPropertySnapshotTests(legacy_tests.LegacyUpgradeCase):
    def response(self):
        return [
            'Id=xray.service', 'LoadState=loaded',
            'ActiveState=' + self.env['MOCK_SERVICE_STATE'],
            f'ExecStart={{ path={self.binary} ; argv[]={self.binary} run -config {self.runtime} ; ignore_errors=no ; pid=0 ; }}',
            'User=root', 'Group=', 'WorkingDirectory=', 'RootDirectory=',
            'Environment=XRAY_LOCATION_ASSET=' + str(self.geodata),
            'Description=' + SECRET, 'UnrelatedPrivateValue=' + SECRET,
        ]

    def query(self, lines, *, exit_code=0):
        self.env['FIXTURE_FULL_RESPONSE'] = '\n'.join(lines) + '\n'
        self.env['FIXTURE_FULL_EXIT'] = str(exit_code)
        extra = r'''
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    if [[ "$*" == '--no-pager --full --all show xray' ]]; then
        printf 'systemctl %s\n' "$*" >> "$CALLS_FILE"
        printf '%s' "$FIXTURE_FULL_RESPONSE"
        echo fixture-sensitive-marker-never-print >&2
        return "$FIXTURE_FULL_EXIT"
    fi
    fixture_systemctl "$@"
}
'''
        return self.run_upgrade(extra=extra)

    def assert_redacted(self, result):
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNotIn(SECRET, self.calls())
        for path in self.state_dir.rglob('*'):
            if path.is_file():
                self.assertNotIn(SECRET.encode(), path.read_bytes(), str(path))
        self.assertNotIn(' RootImage ', self.calls())

    def assert_rejected(self, result, stage):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(stage, result.stderr)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assert_preserved_data()
        self.assert_no_service_mutation()
        self.assertNotIn('curl ', self.calls())
        self.assert_redacted(result)

    def test_full_show_accepts_old_219_missing_optional_properties(self):
        result = self.query(self.response())
        self.assert_success(result)
        self.assert_preserved_data(version='v26.10.21')
        self.assertEqual(self.calls().count('systemctl ' + FULL_SHOW), 2)
        self.assert_redacted(result)

    def test_empty_modern_optional_properties_are_supported(self):
        result = self.query(self.response() + [name + '=' for name in OPTIONAL])
        self.assert_success(result)
        self.assert_redacted(result)

    def test_full_show_failure_is_not_treated_as_absent_optional_properties(self):
        for lines in [[], self.response()]:
            with self.subTest(valid_looking_stdout=bool(lines)):
                self.assert_rejected(self.query(lines, exit_code=1), 'query.properties')

    def test_each_required_scalar_must_be_present_even_if_normally_empty(self):
        for key in REQUIRED:
            with self.subTest(missing=key):
                lines = [line for line in self.response() if not line.startswith(key + '=')]
                self.assert_rejected(self.query(lines), 'missing.' + key)

    def test_selected_properties_must_not_be_duplicated_even_with_equal_values(self):
        for key in REQUIRED + OPTIONAL:
            with self.subTest(duplicate=key):
                lines = self.response()
                value = next((line for line in lines if line.startswith(key + '=')), key + '=')
                if key in OPTIONAL:
                    lines.append(value)
                lines.append(value)
                self.assert_rejected(self.query(lines), 'duplicate.' + key)

    def test_invalid_identity_load_and_activity_sentinels_fail_closed(self):
        for key, value in [('Id', 'unrelated.service'), ('Id', ''), ('LoadState', 'not-found'),
                           ('LoadState', 'masked'), ('LoadState', ''), ('ActiveState', 'activating'),
                           ('ActiveState', 'deactivating'), ('ActiveState', '')]:
            with self.subTest(key=key, value=value):
                lines = [key + '=' + value if line.startswith(key + '=') else line for line in self.response()]
                self.assert_rejected(self.query(lines), 'unsupported.' + key)

    def test_malformed_selected_property_lines_are_not_ignored(self):
        for malformed in ['RootImage', 'LoadState', 'EnvironmentFiles', 'EnvironmentFile',
                          'ExecStartPre', 'EnvironmentFile ' + SECRET]:
            with self.subTest(malformed=malformed):
                self.assert_rejected(self.query(self.response() + [malformed]), 'format.properties')

    def test_nonempty_optional_properties_and_219_singular_environment_file_reject(self):
        for key in OPTIONAL + ['EnvironmentFile']:
            with self.subTest(key=key):
                self.assert_rejected(self.query(self.response() + [key + '=/' + SECRET]),
                                     'unsupported.' + ('EnvironmentFiles' if key == 'EnvironmentFile' else key))

    def test_empty_user_is_root_but_other_missing_identity_is_not_assumed(self):
        lines = ['User=' if line.startswith('User=') else line for line in self.response()]
        result = self.query(lines)
        self.assert_success(result)
        self.assert_redacted(result)

    def test_unrelated_sensitive_full_properties_are_never_passed_to_external_argv(self):
        extra = r'''
jq() { builtin printf '%s\n' "$*" >> "$HOME/external-argv"; command jq "$@"; }
awk() { builtin printf '%s\n' "$*" >> "$HOME/external-argv"; command awk "$@"; }
sed() { builtin printf '%s\n' "$*" >> "$HOME/external-argv"; command sed "$@"; }
grep() { builtin printf '%s\n' "$*" >> "$HOME/external-argv"; command grep "$@"; }
'''
        # Default full-show mock already contains an unrelated private fixture.
        result = self.run_upgrade(extra=extra)
        self.assert_success(result)
        self.assertNotIn('unrelated-private-fullshow-fixture', (self.home / 'external-argv').read_text())
        for path in self.state_dir.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'unrelated-private-fullshow-fixture', path.read_bytes())


class FullScriptSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='xray-wrapper-snapshot-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        state_dir = self.home / '.xray-script'
        state_dir.mkdir()
        self.state = state_dir / 'config.json'
        self.state.write_text(json.dumps({'language': 'zh', 'xray': {'version': 'v26.10.21'}, 'preserve': True}))
        self.original_state = self.state.read_bytes()
        (self.home / 'snapshot').mkdir()
        (self.home / 'config-fixture.json').write_text('{"preserve":"runtime"}')
        (self.home / 'unit-fixture').write_text(
            '[Service]\nUser=root\nExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json\n')

    def run_snapshot(self, *, route='handler', full_exit=0, old_query=False, trace=False):
        mocks = r'''
systemctl() {
    printf 'systemctl %s\n' "$*" >> "$HOME/calls"
    # Affected host rejects even paired filtered queries with unknown names.
    if [[ " $* " == *' RootImage '* ]]; then echo "$FIXTURE_SECRET" >&2; return 1; fi
    if [[ "$*" == '--no-pager --full --all show xray' ]]; then
        printf 'Id=xray.service\nLoadState=loaded\nActiveState=inactive\n%s\n' "$FIXTURE_EXEC_START"
        printf 'User=root\nGroup=\nEnvironment=\nWorkingDirectory=\nRootDirectory=\n'
        printf 'Description=%s\nUnrelatedPrivateValue=%s\n' "$FIXTURE_SECRET" "$FIXTURE_SECRET"
        echo "$FIXTURE_SECRET" >&2
        return "$FIXTURE_FULL_EXIT"
    fi
    case "$*" in
    '--no-pager --full show -p ActiveState xray') echo 'ActiveState=inactive' ;;
    '--no-pager --full cat xray') command cat "$HOME/unit-fixture" ;;
    *) echo UNSAFE_SYSTEMCTL_CALL >&2; return 98 ;;
    esac
}
cp() {
    [[ "$*" == "-- /usr/local/etc/xray/config.json $HOME/snapshot/config.before" ]] || return 98
    command cp "$HOME/config-fixture.json" "$HOME/snapshot/config.before"
}
sha256sum() {
    # Never read canonical host assets, even if they happen to exist.
    case "$1" in /usr/local/share/xray/geoip.dat|/usr/local/share/xray/geosite.dat)
        printf '%064d  %s\n' 0 "$1" ;; *) return 98 ;; esac
}
id() { [[ "$*" == root ]]; }
xray_install_platform() { echo centos:7; }
cmd_exists() { [[ "$1" == xray ]]; }
run_xray_installer() { echo UNSAFE_INSTALLER_CALL >&2; return 98; }
legacy_upgrade_xray() {
    printf 'ROUTE %s %s %s\n' "$1" "${CUR_FILE:-unset}" "${XRAY_CONFIG_PATH:-unset}" >> "$HOME/calls"
    [[ "$FIXTURE_TRACE" != 1 ]] || set -x
    legacy_service_snapshot /usr/local/bin/xray /usr/local/etc/xray/config.json "$HOME/snapshot"
}
curl() {
    [[ "$*" == *api.github.com/repos/XTLS/Xray-core/releases/latest* ]] || return 98
    printf '{"tag_name":"v26.10.21"}\n'
}
'''
        if old_query:
            mocks += r'''
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    if [[ "$*" == '--no-pager --full --all show xray' ]]; then
        # Negative control substitutes the failed .3 query, not a copied parser.
        fixture_systemctl --no-pager --full show -p LoadState -p RootImage xray
    else
        fixture_systemctl "$@"
    fi
}
'''
        handler = 'source ' + shlex.quote(str(ROOT / 'core/handler.sh')) + ' --probe\n' + mocks
        env = os.environ.copy()
        env.update(HOME=str(self.home), FIXTURE_EXEC_START=USER_EXEC_START,
                   FIXTURE_FULL_EXIT=str(full_exit), FIXTURE_SECRET=SECRET, FIXTURE_TRACE='1' if trace else '0')
        script = handler + '\nmain --install v26.10.21 y'
        entry = ROOT / 'core/handler.sh'
        if route == 'main':
            env['HANDLER_PROBE_SCRIPT'] = handler + '\nmain "$@"'
            script = r'''
bash() {
    case "$1" in
    */menu.sh)
        case "$2" in --index|--xray) return 2 ;; --banner|--status) return 0 ;; *) return 98 ;; esac ;;
    */handler.sh)
        local script="$1"; shift
        command bash --noprofile --norc -c "$HANDLER_PROBE_SCRIPT" "$script" "$@" ;;
    *) echo UNSAFE_BASH_CALL >&2; return 98 ;;
    esac
}
''' + '\nsource ' + shlex.quote(str(ROOT / 'core/main.sh'))
            entry = ROOT / 'core/main.sh'
        return subprocess.run(['bash', '--noprofile', '--norc', '-c', script, str(entry)],
                              env=env, capture_output=True, text=True, timeout=5)

    def assert_safe(self, result):
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNotIn('UNSAFE_', result.stdout + result.stderr)
        self.assertEqual(self.state.read_bytes(), self.original_state)
        calls = (self.home / 'calls').read_text()
        self.assertNotIn(' RootImage ', calls)
        self.assertIn('systemctl ' + FULL_SHOW, calls)
        for action in ['stop', 'start', 'restart', 'enable', 'daemon-reload']:
            self.assertNotIn('systemctl ' + action, calls)
        for path in (self.home / 'snapshot').rglob('*'):
            if path.is_file():
                self.assertNotIn(SECRET.encode(), path.read_bytes())

    def test_actual_handler_globals_accept_users_exact_utf8_unit_fixture(self):
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_safe(result)
        self.assertIn(USER_EXEC_START, (self.home / 'snapshot/properties.before').read_text())
        self.assertEqual((self.home / 'snapshot/config.before').read_bytes(), (self.home / 'config-fixture.json').read_bytes())
        self.assertIn('ROUTE v26.10.21 handler /usr/local/etc/xray/config.json', (self.home / 'calls').read_text())

    def test_actual_main_to_handler_avoids_all_unknown_property_queries(self):
        result = self.run_snapshot(route='main')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_safe(result)
        self.assertIn(USER_EXEC_START, (self.home / 'snapshot/properties.before').read_text())

    def test_actual_wrapper_rejects_full_query_error_without_writes_or_secrets(self):
        result = self.run_snapshot(route='main', full_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assert_safe(result)
        self.assertIn('query.properties', result.stderr)
        self.assertFalse((self.home / 'snapshot/unit.before').exists())
        self.assertFalse((self.home / 'snapshot/config.before').exists())

    def test_direct_snapshot_disables_inherited_trace_before_sensitive_full_show(self):
        result = self.run_snapshot(trace=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('set +x', result.stderr)
        self.assert_safe(result)

    def test_historical_paired_query_remains_a_failure_on_affected_host_fixture(self):
        result = self.run_snapshot(old_query=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('query.properties', result.stderr)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertFalse((self.home / 'snapshot/unit.before').exists())
        self.assertFalse((self.home / 'snapshot/config.before').exists())
        self.assertEqual(self.state.read_bytes(), self.original_state)
        self.assertIn(' -p RootImage ', (self.home / 'calls').read_text())


class SnapshotDiagnosticTests(legacy_tests.LegacyUpgradeCase):
    def assert_snapshot_rejected(self, result, stage):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(stage, result.stderr)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assert_preserved_data()
        self.assert_no_service_mutation()
        self.assertNotIn('curl ', self.calls())

    def test_full_show_permission_error_is_not_suppressed(self):
        extra = r'''
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    if [[ "$*" == '--no-pager --full --all show xray' ]]; then
        echo fixture-sensitive-marker-never-print >&2; return 1
    fi
    fixture_systemctl "$@"
}
'''
        self.assert_snapshot_rejected(self.run_upgrade(extra=extra), 'query.properties')

    def test_nonempty_modern_root_image_remains_unsupported(self):
        self.assert_snapshot_rejected(self.run_upgrade(extra=self.override_property('RootImage', '/' + SECRET + '.raw')), 'unsupported.RootImage')

    def test_unsupported_exec_start_and_environment_values_are_not_echoed(self):
        for name, value, stage in [
            ('ExecStart', '{ path=/' + SECRET + ' ; argv[]=/unapproved ; }', 'unsupported.ExecStart'),
            ('Environment', 'PASSWORD=' + SECRET, 'unsupported.Environment'),
        ]:
            with self.subTest(property=name):
                self.assert_snapshot_rejected(self.run_upgrade(extra=self.override_property(name, value)), stage)

    def test_unit_read_failure_reports_read_unit_before_download(self):
        extra = r'''
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    if [[ " $* " == *' cat '* ]]; then echo fixture-sensitive-marker-never-print >&2; return 1; fi
    fixture_systemctl "$@"
}
'''
        self.assert_snapshot_rejected(self.run_upgrade(extra=extra), 'read.unit')

    def test_config_copy_failure_reports_copy_config_before_download(self):
        extra = r'''
cp() {
    [[ "$1 $2" != "-- $XRAY_CONFIG_PATH" ]] || return 1
    command cp "$@"
}
'''
        self.assert_snapshot_rejected(self.run_upgrade(extra=extra), 'copy.config')

    def test_geodata_read_failure_reports_named_stage_before_download(self):
        extra = r'''
sha256sum() {
    [[ "$1" != "$DAT_PATH/geoip.dat" ]] || return 1
    command sha256sum "$@"
}
'''
        self.assert_snapshot_rejected(self.run_upgrade(extra=extra), 'hash.geoip.dat')


if __name__ == '__main__':
    unittest.main()
