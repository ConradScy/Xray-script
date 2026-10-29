#!/usr/bin/env python3
"""CentOS 7 property-query regressions, with no real service or network access.

The wrapper fixture sources the complete scripts (globals and entry points),
then exercises the real main -> handler child-process dispatch. Its upgrade
boundary runs only the real read-only snapshot function; all systemctl, account,
asset, download and canonical-config accesses are temporary-file mocks.
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


PAIR = 'show -p LoadState -p RootImage xray --no-pager --full'
SECRET = 'fixture-sensitive-marker-never-print'
USER_EXEC_START = (
    'ExecStart={ path=/usr/local/bin/xray ; '
    'argv[]=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json ; '
    'ignore_errors=no ; start_time=[四 2026-10-29 18:01:53 CST] ; '
    'stop_time=[四 2026-10-29 18:25:02 CST] ; pid=4674 ; code=exited ; status=0 }'
)


class OptionalRootImageTests(unittest.TestCase):
    def query(self, response, *, exit_code=0):
        script = 'source ' + shlex.quote(str(ROOT / 'core/platform.sh')) + r'''
systemctl() {
    [[ "$*" == 'show -p LoadState -p RootImage xray --no-pager --full' ]] || return 98
    printf '%s' "$FIXTURE_RESPONSE"
    printf '%s\n' "$FIXTURE_SECRET" >&2
    return "$FIXTURE_EXIT"
}
xray_optional_root_image
'''
        env = os.environ.copy()
        env.update(FIXTURE_RESPONSE=response, FIXTURE_EXIT=str(exit_code), FIXTURE_SECRET=SECRET)
        return subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                              env=env, capture_output=True, text=True, timeout=5)

    def assert_rejected(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertEqual(result.stdout, '')

    def test_known_loaded_property_allows_absent_unsupported_root_image(self):
        result = self.query('LoadState=loaded\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertNotIn(SECRET, result.stderr)

    def test_modern_empty_root_image_accepts_either_property_order(self):
        for response in ['LoadState=loaded\nRootImage=\n', 'RootImage=\nLoadState=loaded\n']:
            with self.subTest(response=response):
                result = self.query(response)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, '')

    def test_query_failure_is_rejected_even_when_stdout_looks_valid(self):
        for response in ['', 'LoadState=loaded\n', 'LoadState=loaded\nRootImage=\n']:
            with self.subTest(response=response):
                result = self.query(response, exit_code=1)
                self.assert_rejected(result)
                self.assertIn('RootImage.query-failed', result.stderr)

    def test_missing_load_state_never_proves_optional_property_absent(self):
        for response in ['', 'RootImage=\n']:
            with self.subTest(response=response):
                self.assert_rejected(self.query(response))

    def test_duplicate_properties_are_rejected_even_if_values_match(self):
        for response in [
            'LoadState=loaded\nLoadState=loaded\n',
            'LoadState=loaded\nRootImage=\nRootImage=\n',
            'RootImage=\nLoadState=loaded\nRootImage=\n',
        ]:
            with self.subTest(response=response):
                self.assert_rejected(self.query(response))

    def test_not_loaded_or_malformed_states_are_rejected(self):
        for state in ['not-found', 'masked', 'error', '', 'loaded extra', 'loaded\r']:
            with self.subTest(state=state):
                result = self.query('LoadState=' + state + '\n')
                self.assert_rejected(result)
                self.assertIn('RootImage.invalid-response', result.stderr)

    def test_unexpected_all_properties_response_is_not_mistaken_for_absence(self):
        for response in [
            'LoadState=loaded\nEnvironment=' + SECRET + '\n',
            'LoadState=loaded\nDescription=xray\n',
            'LoadState=loaded\nRootImage\n',
            ' LoadState=loaded\n',
            'LoadState=loaded\n\nRootImage=\n',
            'LoadState=loaded\nRootImage=\ninvalid=' + SECRET + '\n',
        ]:
            with self.subTest(response=response):
                result = self.query(response)
                self.assert_rejected(result)
                self.assertIn('RootImage.invalid-response', result.stderr)


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

    def run_snapshot(self, *, route='handler', singleton='fail', paired_exit=0, old_query=False):
        mocks = r'''
systemctl() {
    printf 'systemctl %s\n' "$*" >> "$HOME/calls"
    if [[ "$*" == 'show -p LoadState -p RootImage xray --no-pager --full' ]]; then
        printf 'LoadState=loaded\n'
        return "$FIXTURE_PAIRED_EXIT"
    fi
    case "$1" in
    show)
        [[ "$*" == *' --no-pager --full' ]] || return 98
        case "$3" in
        ExecStart) printf '%s\n' "$FIXTURE_EXEC_START" ;;
        ActiveState) echo 'ActiveState=inactive' ;;
        User) echo 'User=root' ;;
        Group|Environment|WorkingDirectory|RootDirectory) printf '%s=\n' "$3" ;;
        EnvironmentFiles|ExecStartPre|ExecStartPost) return 0 ;;
        RootImage)
            if [[ "$FIXTURE_SINGLETON" == all-properties ]]; then
                printf 'LoadState=loaded\nEnvironment=%s\n' "$FIXTURE_SECRET"
            else
                printf '%s\n' "$FIXTURE_SECRET" >&2
                return 1
            fi ;;
        *) echo UNEXPECTED_PROPERTY >&2; return 97 ;;
        esac ;;
    cat)
        [[ "$*" == 'cat xray --no-pager --full' ]] || return 98
        command cat "$HOME/unit-fixture" ;;
    *) echo UNSAFE_SYSTEMCTL_CALL >&2; return 98 ;;
    esac
}
cp() {
    [[ "$*" == "-- /usr/local/etc/xray/config.json $HOME/snapshot/config.before" ]] || return 98
    command cp "$HOME/config-fixture.json" "$HOME/snapshot/config.before"
}
sha256sum() {
    # The exact user fixture has empty Environment and canonical asset paths.
    # If those host paths exist, never read them: this mock supplies public data.
    case "$1" in /usr/local/share/xray/geoip.dat|/usr/local/share/xray/geosite.dat)
        printf '%064d  %s\n' 0 "$1" ;; *) return 98 ;; esac
}
id() { [[ "$*" == root ]]; }
xray_install_platform() { echo centos:7; }
cmd_exists() { [[ "$1" == xray ]]; }
run_xray_installer() { echo UNSAFE_INSTALLER_CALL >&2; return 98; }
legacy_upgrade_xray() {
    printf 'ROUTE %s %s %s\n' "$1" "${CUR_FILE:-unset}" "${XRAY_CONFIG_PATH:-unset}" >> "$HOME/calls"
    legacy_service_snapshot /usr/local/bin/xray /usr/local/etc/xray/config.json "$HOME/snapshot"
}
curl() {
    [[ "$*" == *api.github.com/repos/XTLS/Xray-core/releases/latest* ]] || return 98
    printf '{"tag_name":"v26.10.21"}\n'
}
'''
        if old_query:
            # Negative control: the original singleton API is still real code.
            # This intentionally restores only the faulty query choice, not a
            # copy of the production snapshot/parser implementation.
            mocks += '\nxray_optional_root_image() { xray_service_property RootImage optional; }\n'
        handler = 'source ' + shlex.quote(str(ROOT / 'core/handler.sh')) + ' --probe\n' + mocks
        env = os.environ.copy()
        env.update(HOME=str(self.home), FIXTURE_EXEC_START=USER_EXEC_START,
                   FIXTURE_SINGLETON=singleton, FIXTURE_PAIRED_EXIT=str(paired_exit), FIXTURE_SECRET=SECRET)
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
        self.assertNotIn('show -p RootImage ', calls)
        self.assertIn('systemctl ' + PAIR, calls)
        for action in ['stop', 'start', 'restart', 'enable', 'daemon-reload']:
            self.assertNotIn('systemctl ' + action, calls)

    def test_actual_handler_globals_accept_users_exact_utf8_unit_fixture(self):
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_safe(result)
        props = (self.home / 'snapshot/properties.before').read_text()
        self.assertIn(USER_EXEC_START, props)
        self.assertEqual((self.home / 'snapshot/config.before').read_bytes(), (self.home / 'config-fixture.json').read_bytes())
        self.assertIn('ROUTE v26.10.21 handler /usr/local/etc/xray/config.json', (self.home / 'calls').read_text())

    def test_actual_main_to_handler_subprocess_avoids_singleton_unknown_query(self):
        result = self.run_snapshot(route='main', singleton='all-properties')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_safe(result)
        self.assertIn(USER_EXEC_START, (self.home / 'snapshot/properties.before').read_text())

    def test_actual_wrapper_rejects_paired_query_error_without_writes_or_secrets(self):
        result = self.run_snapshot(route='main', paired_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assert_safe(result)
        self.assertIn('RootImage.query-failed', result.stderr)
        self.assertFalse((self.home / 'snapshot/unit.before').exists())
        self.assertFalse((self.home / 'snapshot/config.before').exists())
        self.assertEqual((self.home / 'snapshot/properties.before').read_text().splitlines(),
                         [USER_EXEC_START, 'EnvironmentFiles=', 'RootDirectory='])

    def test_historical_singleton_query_reproduces_users_three_line_backup(self):
        for mode in ['fail', 'all-properties']:
            with self.subTest(singleton=mode):
                result = self.run_snapshot(singleton=mode, old_query=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('query.RootImage', result.stderr)
                self.assertNotIn(SECRET, result.stdout + result.stderr)
                self.assertEqual((self.home / 'snapshot/properties.before').read_text().splitlines(),
                                 [USER_EXEC_START, 'EnvironmentFiles=', 'RootDirectory='])
                self.assertFalse((self.home / 'snapshot/unit.before').exists())
                self.assertFalse((self.home / 'snapshot/config.before').exists())
                self.assertEqual(self.state.read_bytes(), self.original_state)
                calls = (self.home / 'calls').read_text()
                self.assertIn('show -p RootImage xray --no-pager --full', calls)
                for action in ['stop', 'start', 'restart', 'enable', 'daemon-reload']:
                    self.assertNotIn('systemctl ' + action, calls)


class SnapshotDiagnosticTests(legacy_tests.LegacyUpgradeCase):
    def assert_snapshot_rejected(self, result, stage):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(stage, result.stderr)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assert_preserved_data()
        self.assert_no_service_mutation()
        self.assertNotIn('curl ', self.calls())

    def test_paired_root_image_permission_error_is_distinct_from_unsupported_property(self):
        extra = r'''
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    if [[ "$*" == 'show -p LoadState -p RootImage xray --no-pager --full' ]]; then
        printf 'LoadState=loaded\n'
        echo fixture-sensitive-marker-never-print >&2
        return 1
    fi
    fixture_systemctl "$@"
}
'''
        self.assert_snapshot_rejected(self.run_upgrade(extra=extra), 'RootImage.query-failed')

    def test_nonempty_modern_root_image_remains_unsupported(self):
        result = self.run_upgrade(extra=self.override_property('RootImage', '/' + SECRET + '.raw'))
        self.assert_snapshot_rejected(result, 'unsupported.RootImage')

    def test_exec_start_query_error_reports_stage_without_external_diagnostic(self):
        extra = r'''
eval "$(declare -f systemctl | sed '1s/systemctl/fixture_systemctl/')"
systemctl() {
    if [[ "$1 $2 $3" == 'show -p ExecStart' ]]; then
        echo fixture-sensitive-marker-never-print >&2; return 1
    fi
    fixture_systemctl "$@"
}
'''
        self.assert_snapshot_rejected(self.run_upgrade(extra=extra), 'query.ExecStart')

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
    if [[ "$1" == cat ]]; then echo fixture-sensitive-marker-never-print >&2; return 1; fi
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
