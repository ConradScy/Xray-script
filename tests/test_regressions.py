#!/usr/bin/env python3
"""Safe offline regressions: no installer main(), services, or host configuration.

Run: python3 -m unittest discover -s tests -v
Optional official parser check (read-only, never execute the installer):
  XRAY_INSTALLER_SOURCE=/path/to/install-release.sh python3 -m unittest discover -s tests -v
"""

import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def functions_from(path):
    """Load function definitions only, never a script's globals or main call.

    These repository scripts use unindented closing function braces. Keeping
    extraction intentionally narrow prevents executing their installation entry
    points, even when a new top-level statement is added.
    """
    source = Path(path).read_text()
    functions = re.findall(
        r"^(?:function\s+)?[A-Za-z_][A-Za-z_0-9]*\s*\(\)\s*\{\n.*?^\}",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not functions:
        raise AssertionError(f"No shell functions found in {path}")
    return "\n\n".join(functions)


def named_function(path, name):
    source = functions_from(path)
    match = re.search(
        rf"^(?:function\s+)?{re.escape(name)}\s*\(\)\s*\{{\n.*?^\}}",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"Missing function {name} in {path}")
    return match.group(0)


class ShellCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xray-script-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.config_file = self.home / "config.json"
        self.initial = json.loads((ROOT / "config.json").read_text())
        self.initial.update(language="en", version="v2026.03.17")
        self.initial["xray"].update(
            version="v25.3.6", uuid="public-fixture-uuid", privateKey="public-fixture-key"
        )
        self.initial["custom_fixture"] = {"preserve": True}
        self.config_file.write_text(json.dumps(self.initial))
        self.env = os.environ.copy()
        self.env.update(
            HOME=str(self.home),
            TMPDIR=str(self.home),
            SCRIPT_CONFIG_PATH=str(self.config_file),
            CALLS_FILE=str(self.home / "calls"),
            DAT_PATH=str(self.home / "geodata"),
        )

    def run_shell(self, body, *, file="core/handler.sh", prelude=""):
        script = "\n".join(
            [
                functions_from(ROOT / file),
                "source " + shlex.quote(str(ROOT / "core/platform.sh")) if file == "core/handler.sh" else ":",
                "declare -A CONFIG_DATA=()",
                "SCRIPT_CONFIG=$(cat \"$SCRIPT_CONFIG_PATH\")",
                "I18N_DATA='{}'; CUR_FILE=handler; GREEN=; RED=; NC=",
                "sleep() { :; }",
                "_error() { printf '%s\\n' \"$*\" >&2; exit 1; }",
                "curl() { echo 'unexpected network request' >&2; return 99; }",
                "systemctl() { echo 'unexpected systemctl' >&2; return 99; }",
                "xray_install_platform() { printf 'debian:12\\n'; }",
                "xray_sysusers_preflight() { return 0; }",
                "xray() { echo 'unexpected xray call' >&2; return 99; }",
                prelude,
                body,
            ]
        )
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", script],
            cwd=self.home,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class RepositoryChecks(unittest.TestCase):
    def test_shell_syntax(self):
        for path in sorted(ROOT.rglob("*.sh")):
            if ".git" in path.parts:
                continue
            with self.subTest(path=str(path.relative_to(ROOT))):
                result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_json_syntax(self):
        for path in sorted(ROOT.rglob("*.json")):
            if ".git" not in path.parts:
                with self.subTest(path=str(path.relative_to(ROOT))):
                    json.loads(path.read_text())

    def test_fork_downloads_and_documented_command(self):
        install = (ROOT / "install.sh").read_text()
        self.assertIn("ConradScy/Xray-script", install)
        # Attribution links may remain; executable upstream download URLs may not.
        self.assertNotRegex(install, r"https://(?:raw\.githubusercontent\.com|api\.github\.com/repos)/zxcvos/",)
        for path in [ROOT / "README.md", ROOT / ".github/README.en.md"]:
            with self.subTest(path=path.name):
                readme = path.read_text()
                self.assertIn("https://raw.githubusercontent.com/ConradScy/Xray-script/main/install.sh", readme)
                self.assertNotIn("--no-check-certificate", readme)
                self.assertNotIn("raw.githubusercontent.com/zxcvos/Xray-script", readme)


class OfficialInstallerParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = os.environ.get("XRAY_INSTALLER_SOURCE")
        if not source:
            raise unittest.SkipTest("set XRAY_INSTALLER_SOURCE for official parser contract tests")
        cls.parser = named_function(Path(source), "judgment_parameters")

    def parse(self, args):
        # Initialize only parser inputs; no upstream global code or main executes.
        setup = """
INSTALL=0 UPDATE_GEODATA=0 HELP=0 CHECK=0 REMOVE=0
NO_GEODATA=0 NO_LOGFILES=0 PURGE=0 FORCE=0 BETA=0 REINSTALL=0
N_UP_SERVICE=0 LOGROTATE=0 NO_RESTART=0
SPECIFIED_VERSION='' LOCAL_FILE='' LOCAL_GEOIP='' LOCAL_GEOSITE='' PROXY=''
"""
        script = setup + self.parser + "\njudgment_parameters " + shlex.join(args)
        script += "\nstatus=$?; printf '\\nRESULT:%s:%s:%s:%s\\n' \"$status\" \"$N_UP_SERVICE\" \"$SPECIFIED_VERSION\" \"$NO_GEODATA\"; exit \"$status\""
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)

    def test_removed_runtime_user_is_rejected(self):
        result = self.parse(["install", "-u", "root", "--version", "v26.10.21"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown option", result.stdout)

    def test_supported_options_preserve_existing_unit(self):
        result = self.parse(["install", "--no-update-service", "--version", "v26.10.21"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RESULT:0:1:v26.10.21", result.stdout)


    def test_preserve_geodata_option_is_supported(self):
        result = self.parse(['install', '--no-update-service', '--version', 'v26.10.21', '--without-geodata'])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('RESULT:0:1:v26.10.21:1', result.stdout)


class InstallerDownloadTests(ShellCase):
    def curl_fixture(self, content, status=0):
        # Only write controlled test data to the helper's temporary output path.
        return f"""
curl() {{
    local output=''
    while [[ $# -gt 0 ]]; do
        case "$1" in -o|--output) output=$2; shift ;; esac
        shift
    done
    [[ -n "$output" && "$output" == "$TMPDIR/"* ]] || return 98
    printf '%s' {shlex.quote(content)} > "$output"
    return {status}
}}
"""

    def test_downloaded_installer_gets_exact_arguments_and_cleans_up(self):
        script = '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$CALLS_FILE"\n'
        result = self.run_shell(
            'run_xray_installer install --no-update-service --version v26.10.21',
            prelude=self.curl_fixture(script),
        )
        self.assert_success(result)
        self.assertEqual((self.home / 'calls').read_text().splitlines(),
                         ['install', '--no-update-service', '--version', 'v26.10.21'])
        self.assertEqual({p.name for p in self.home.iterdir()}, {'calls', 'config.json'})

    def test_failed_download_is_not_executed(self):
        script = '#!/usr/bin/env bash\ntouch "$CALLS_FILE"\n'
        result = self.run_shell('run_xray_installer install', prelude=self.curl_fixture(script, 22))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / 'calls').exists())
        self.assertEqual({p.name for p in self.home.iterdir()}, {'config.json'})

    def test_empty_or_invalid_download_is_not_executed(self):
        for data in ['', '#!/usr/bin/env bash\nif then\n']:
            with self.subTest(data=data):
                result = self.run_shell('run_xray_installer install', prelude=self.curl_fixture(data))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.home / 'calls').exists())
                self.assertEqual({p.name for p in self.home.iterdir()}, {'config.json'})

    def test_installer_failure_is_propagated_and_cleans_up(self):
        result = self.run_shell('run_xray_installer install',
                                prelude=self.curl_fixture('#!/usr/bin/env bash\nexit 42\n'))
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertEqual({p.name for p in self.home.iterdir()}, {'config.json'})


class VersionAndInstallTests(ShellCase):
    def mock_installer(self, *, code=0, installed='26.10.21', exists=True):
        return f"""
run_xray_installer() {{ printf '%s\\n' "$@" > "$CALLS_FILE"; return {code}; }}
cmd_exists() {{ return {0 if exists else 1}; }}
xray() {{ [[ "$1" == version ]] || return 97; printf 'Xray {installed} (Xray, Penetrates Everything.) test\\n'; }}
"""

    def test_explicit_version_selects_without_fetching_or_persisting(self):
        before = self.config_file.read_bytes()
        result = self.run_shell("handler_xray_version v26.10.21 n; printf '%s' \"${CONFIG_DATA[version]}\"")
        self.assert_success(result)
        self.assertTrue(result.stdout.endswith('v26.10.21'), result.stdout)
        self.assertEqual(self.config_file.read_bytes(), before)

    def test_custom_bare_version_is_normalized(self):
        result = self.run_shell('handler_xray_version custom n; printf "%s" "${CONFIG_DATA[version]}"',
            prelude="exec_read() { CONFIG_DATA[version]='26.10.21'; }")
        self.assert_success(result)
        self.assertTrue(result.stdout.endswith('v26.10.21'), result.stdout)

    def test_saved_bare_version_is_normalized(self):
        self.initial['xray']['version'] = '26.10.21'
        self.config_file.write_text(json.dumps(self.initial))
        result = self.run_shell("handler_install '' y", prelude=self.mock_installer())
        self.assert_success(result)
        self.assertEqual((self.home / 'calls').read_text().splitlines()[-1], 'v26.10.21')
        self.assertEqual(json.loads(self.config_file.read_text())['xray']['version'], 'v26.10.21')

    def test_failed_version_command_cannot_claim_success(self):
        before = self.config_file.read_bytes()
        result = self.run_shell('handler_install v26.10.21 y', prelude=self.mock_installer() + """
xray() { printf 'Xray 26.10.21 (fixture)\n'; return 1; }
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config_file.read_bytes(), before)

    def test_release_and_latest_resolution(self):
        for selection, payload in [
            ('release', '{"tag_name":"v26.10.21"}'),
            ('latest', '[{"tag_name":"v26.10.21","draft":false}]'),
        ]:
            with self.subTest(selection=selection):
                result = self.run_shell(
                    f"handler_xray_version {selection} n; printf '%s' \"${{CONFIG_DATA[version]}}\"",
                    prelude=f"curl() {{ printf '%s' {shlex.quote(payload)}; }}",
                )
                self.assert_success(result)
                self.assertTrue(result.stdout.endswith('v26.10.21'), result.stdout)

    def test_invalid_api_or_explicit_version_preserves_config(self):
        before = self.config_file.read_bytes()
        for selection, payload in [
            ('release', '{"message":"API rate limit exceeded"}'),
            ('release', '<html>service unavailable</html>'),
            ('latest', '[]'),
            ('release', '{"tag_name":"null"}'),
            ('invalid-version', '{}'),
        ]:
            with self.subTest(selection=selection, payload=payload):
                result = self.run_shell(f"handler_xray_version {selection}",
                    prelude=f"curl() {{ printf '%s' {shlex.quote(payload)}; }}")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.config_file.read_bytes(), before)

    def test_success_uses_supported_flags_and_preserves_other_fields(self):
        result = self.run_shell('handler_install v26.10.21 y', prelude=self.mock_installer())
        self.assert_success(result)
        args = (self.home / 'calls').read_text().splitlines()
        self.assertEqual(args, ['install', '--no-update-service', '--version', 'v26.10.21'])
        expected = self.initial.copy()
        expected['xray'] = {**self.initial['xray'], 'version': 'v26.10.21'}
        self.assertEqual(json.loads(self.config_file.read_text()), expected)

    def test_existing_custom_geodata_is_preserved(self):
        data_dir = self.home / 'custom-geodata'
        data_dir.mkdir()
        fixtures = {'geoip.dat': b'existing custom geoip', 'geosite.dat': b'existing custom geosite'}
        for name, content in fixtures.items():
            (data_dir / name).write_bytes(content)
        self.env['DAT_PATH'] = str(data_dir)
        result = self.run_shell('handler_install v26.10.21 y', prelude=self.mock_installer())
        self.assert_success(result)
        self.assertEqual((self.home / 'calls').read_text().splitlines(),
                         ['install', '--no-update-service', '--version', 'v26.10.21', '--without-geodata'])
        for name, content in fixtures.items():
            self.assertEqual((data_dir / name).read_bytes(), content)

    def test_missing_partial_or_empty_geodata_is_not_skipped(self):
        for index, fixtures in enumerate([
            {},
            {'geoip.dat': b'existing'},
            {'geosite.dat': b'existing'},
            {'geoip.dat': b'', 'geosite.dat': b'existing'},
            {'geoip.dat': b'existing', 'geosite.dat': b''},
            {'geoip.dat': b'', 'geosite.dat': b''},
        ]):
            with self.subTest(fixtures=fixtures):
                data_dir = self.home / f'geodata-{index}'
                data_dir.mkdir()
                for name, content in fixtures.items():
                    (data_dir / name).write_bytes(content)
                self.env['DAT_PATH'] = str(data_dir)
                result = self.run_shell('handler_install v26.10.21 y', prelude=self.mock_installer())
                self.assert_success(result)
                self.assertEqual((self.home / 'calls').read_text().splitlines(),
                                 ['install', '--no-update-service', '--version', 'v26.10.21'])

    def test_geodata_directories_are_not_treated_as_data_files(self):
        data_dir = self.home / 'directory-geodata'
        data_dir.mkdir()
        (data_dir / 'geoip.dat').mkdir()
        (data_dir / 'geosite.dat').mkdir()
        self.env['DAT_PATH'] = str(data_dir)
        result = self.run_shell('handler_install v26.10.21 y', prelude=self.mock_installer())
        self.assert_success(result)
        self.assertNotIn('--without-geodata', (self.home / 'calls').read_text().splitlines())

    def test_installer_failure_does_not_claim_new_version_or_change_credentials(self):
        before = self.config_file.read_bytes()
        result = self.run_shell('handler_install v26.10.21 y', prelude=self.mock_installer(code=42))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config_file.read_bytes(), before)

    def test_wrong_installed_version_is_not_recorded_as_success(self):
        before = self.config_file.read_bytes()
        result = self.run_shell('handler_install v26.10.21 y', prelude=self.mock_installer(installed='25.3.6'))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config_file.read_bytes(), before)

    def test_skipped_install_records_actual_version_not_requested_version(self):
        result = self.run_shell('handler_install v26.10.21 n', prelude=self.mock_installer(installed='25.7.26'))
        self.assert_success(result)
        self.assertFalse((self.home / 'calls').exists())
        self.assertEqual(json.loads(self.config_file.read_text())['xray']['version'], 'v25.7.26')

    def test_fresh_install_invokes_installer_without_removed_user_option(self):
        result = self.run_shell('handler_install v26.10.21 n', prelude=self.mock_installer(exists=False))
        self.assert_success(result)
        args = (self.home / 'calls').read_text().splitlines()
        self.assertNotIn('-u', args)
        self.assertIn('--no-update-service', args)

    def test_quick_install_stops_before_key_and_config_generation_on_failure(self):
        prelude = self.mock_installer(code=42) + """
curl() { printf '%s' '{"tag_name":"v26.10.21"}'; }
handler_script_config() { :; }
handler_x25519_config() { echo keys >> "$CALLS_FILE"; }
handler_xray_config() { echo config >> "$CALLS_FILE"; }
add_rule() { echo rule >> "$CALLS_FILE"; }
handler_geodata_cron() { echo cron >> "$CALLS_FILE"; }
handler_restart() { echo restart >> "$CALLS_FILE"; }
handler_share() { echo share >> "$CALLS_FILE"; }
cmd_exists() { return 1; }
"""
        result = self.run_shell('handler_quick_install Vision', prelude=prelude)
        self.assertNotEqual(result.returncode, 0)
        calls = (self.home / 'calls').read_text().splitlines()
        self.assertEqual(calls, ['install', '--no-update-service', '--version', 'v26.10.21'])


class AtomicConfigTests(ShellCase):
    def test_invalid_json_cannot_replace_existing_config(self):
        before = self.config_file.read_bytes()
        result = self.run_shell("SCRIPT_CONFIG='invalid JSON'; persist_script_config")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config_file.read_bytes(), before)
        self.assertFalse(list(self.home.glob('config.json.tmp.*')))

    def test_failed_rename_cannot_truncate_existing_config(self):
        before = self.config_file.read_bytes()
        result = self.run_shell('SCRIPT_CONFIG=\'{"new":"value"}\'; persist_script_config',
                                prelude='mv() { return 1; }')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config_file.read_bytes(), before)
        self.assertFalse(list(self.home.glob('config.json.tmp.*')))

    def test_success_preserves_config_permissions(self):
        self.config_file.chmod(0o640)
        result = self.run_shell('SCRIPT_CONFIG=\'{"new":"value"}\'; persist_script_config')
        self.assert_success(result)
        self.assertEqual(json.loads(self.config_file.read_text()), {'new': 'value'})
        self.assertEqual(self.config_file.stat().st_mode & 0o777, 0o640)


class BootstrapTests(ShellCase):
    def bootstrap_prelude(self):
        source = (ROOT / 'install.sh').read_text()
        constants = '\n'.join(re.findall(r'^readonly SCRIPT_(?:REPOSITORY|BRANCH|RAW_URL)=.*$', source, re.M))
        return constants + """
unset I18N_DATA
declare -A I18N_DATA=([download]=Download [failed]=Failed [tip]=Notice [new]=Update [now]=Update [completed]=Done)
SCRIPT_CONFIG_DIR="$HOME"
PROJECT_ROOT="$HOME/project"
CUR_DIR="$HOME"
CUR_FILE=bootstrap.sh
"""

    def test_project_archive_url_stays_on_the_fork(self):
        result = self.run_shell('download_xray_script_files "$HOME/project"', file='install.sh',
            prelude=self.bootstrap_prelude() + '\ndownload_github_files() { printf "%s\\n" "$@"; }')
        self.assert_success(result)
        self.assertIn('https://api.github.com/repos/ConradScy/Xray-script/tarball/main', result.stdout)

    def test_failed_update_check_keeps_existing_installation(self):
        project = self.home / 'project'
        project.mkdir()
        (project / 'keep').write_text('previous install')
        before = self.config_file.read_bytes()
        for payload, status in [('<html>error</html>', 0), ('{}', 0), ('', 22)]:
            with self.subTest(payload=payload, status=status):
                result = self.run_shell('check_xray_script_version', file='install.sh',
                    prelude=self.bootstrap_prelude() + f"""
curl() {{ printf '%s' {shlex.quote(payload)}; return {status}; }}
read() {{ echo 'unexpected update prompt' >&2; exit 98; }}
""")
                self.assert_success(result)
                self.assertEqual(self.config_file.read_bytes(), before)
                self.assertEqual((project / 'keep').read_text(), 'previous install')

    def test_update_changes_only_script_version_and_preserves_restart_arguments(self):
        project = self.home / 'project'
        project.mkdir()
        (project / 'keep').write_text('previous install')
        (self.home / 'bootstrap.sh').write_text('old bootstrap')
        result = self.run_shell('check_xray_script_version --vision --lang=en', file='install.sh',
            prelude=self.bootstrap_prelude() + """
curl() { printf '%s' '{"version":"v2026.10.29"}'; }
read() { printf -v "${@: -1}" '%s' y; }
download_xray_script_files() {
    mkdir -p "$1/core"
    printf '#!/usr/bin/env bash\n# new bootstrap\n' > "$1/install.sh"
    printf '%s' '{"version":"v2026.10.30"}' > "$1/config.json"
}
bash() { printf '%s\n' "$@" > "$CALLS_FILE"; }
""")
        self.assert_success(result)
        expected = {**self.initial, 'version': 'v2026.10.30'}
        self.assertEqual(json.loads(self.config_file.read_text()), expected)
        self.assertFalse((project / 'keep').exists())
        self.assertIn('# new bootstrap', (self.home / 'bootstrap.sh').read_text())
        self.assertEqual((self.home / 'calls').read_text().splitlines(),
                         [str(self.home / 'bootstrap.sh'), '--vision', '--lang=en'])
        self.assertFalse(list(self.home.glob('project.backup.*')))

    def test_update_download_failure_does_not_remove_old_project(self):
        project = self.home / 'project'
        project.mkdir()
        (project / 'keep').write_text('previous install')
        before = self.config_file.read_bytes()
        result = self.run_shell('check_xray_script_version', file='install.sh',
            prelude=self.bootstrap_prelude() + """
curl() { printf '%s' '{"version":"v2026.10.29"}'; }
read() { printf -v "${@: -1}" '%s' y; }
download_xray_script_files() { _error 'mock download failed'; }
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config_file.read_bytes(), before)
        self.assertEqual((project / 'keep').read_text(), 'previous install')

    def test_staged_update_config_rename_failure_preserves_old_config(self):
        project = self.home / 'project'
        project.mkdir()
        (project / 'keep').write_text('previous install')
        (self.home / 'bootstrap.sh').write_text('old bootstrap')
        before = self.config_file.read_bytes()
        result = self.run_shell('check_xray_script_version', file='install.sh',
            prelude=self.bootstrap_prelude() + """
curl() { printf '%s' '{"version":"v2026.10.29"}'; }
read() { printf -v "${@: -1}" '%s' y; }
download_xray_script_files() {
    mkdir -p "$1/core"
    printf '#!/usr/bin/env bash\n# new bootstrap\n' > "$1/install.sh"
    printf '%s' '{"version":"v2026.10.29"}' > "$1/config.json"
}
mv() {
    [[ "${@: -1}" != "$SCRIPT_CONFIG_PATH" ]] || return 1
    command mv "$@"
}
bash() { echo 'unexpected restart' > "$CALLS_FILE"; return 98; }
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config_file.read_bytes(), before)
        backups = list(self.home.glob('project.backup.*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / 'keep').read_text(), 'previous install')
        self.assertFalse((self.home / 'calls').exists())

    def test_staged_bootstrap_copy_failure_keeps_previous_bootstrap(self):
        project = self.home / 'project'
        project.mkdir()
        (project / 'keep').write_text('previous install')
        bootstrap = self.home / 'bootstrap.sh'
        bootstrap.write_text('old bootstrap')
        before = self.config_file.read_bytes()
        result = self.run_shell('check_xray_script_version', file='install.sh',
            prelude=self.bootstrap_prelude() + """
curl() { printf '%s' '{"version":"v2026.10.29"}'; }
read() { printf -v "${@: -1}" '%s' y; }
download_xray_script_files() {
    mkdir -p "$1/core"
    printf '#!/usr/bin/env bash\n# new bootstrap\n' > "$1/install.sh"
    printf '%s' '{"version":"v2026.10.29"}' > "$1/config.json"
}
cp() {
    [[ "${@: -2:1}" != "$PROJECT_ROOT/install.sh" ]] || return 1
    command cp "$@"
}
bash() { echo 'unexpected restart' > "$CALLS_FILE"; return 98; }
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(bootstrap.read_text(), 'old bootstrap')
        self.assertEqual(self.config_file.read_bytes(), before)
        backups = list(self.home.glob('project.backup.*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / 'keep').read_text(), 'previous install')
        self.assertFalse((self.home / 'calls').exists())

    def test_invalid_archive_never_overwrites_existing_files(self):
        project = self.home / 'project'
        project.mkdir()
        (project / 'keep').write_text('previous install')
        result = self.run_shell('download_github_files "$PROJECT_ROOT" https://example.invalid/archive', file='install.sh',
            prelude=self.bootstrap_prelude() + """
curl() {
    local output=''
    while [[ $# -gt 0 ]]; do
        case "$1" in -o) output=$2; shift ;; esac
        shift
    done
    printf 'not an archive' > "$output"
}
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((project / 'keep').read_text(), 'previous install')
        self.assertEqual({p.name for p in self.home.iterdir()}, {'config.json', 'project'})

    def test_valid_archive_is_extracted_and_checked(self):
        archive = self.home / 'fixture.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            for path, content in {
                'fixture/install.sh': b'#!/usr/bin/env bash\nexit 0\n',
                'fixture/core/main.sh': b'#!/usr/bin/env bash\nexit 0\n',
                'fixture/config.json': b'{"version":"v2026.10.29"}',
            }.items():
                entry = tarfile.TarInfo(path)
                entry.size = len(content)
                bundle.addfile(entry, io.BytesIO(content))
        result = self.run_shell('download_github_files "$PROJECT_ROOT" https://example.invalid/archive', file='install.sh',
            prelude=self.bootstrap_prelude() + """
curl() {
    local output=''
    while [[ $# -gt 0 ]]; do
        case "$1" in -o) output=$2; shift ;; esac
        shift
    done
    cp "$HOME/fixture.tar.gz" "$output"
}
""")
        self.assert_success(result)
        self.assertTrue((self.home / 'project/core/main.sh').is_file())
        self.assertEqual(json.loads((self.home / 'project/config.json').read_text())['version'], 'v2026.10.29')


class BinaryConfigTests(ShellCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get('XRAY_BINARY')
        if not path:
            raise unittest.SkipTest('set XRAY_BINARY for real Xray run -test validation')
        cls.binary = Path(path).resolve(strict=True)

    def test_all_six_generated_profiles_compile(self):
        # RFC 7748's public Alice test scalar; never generate/deploy credentials.
        fixture_key = 'dwdtCnMYpX08FsFyUbJmRd9ML4frwJkqsXf7pR25LCo'
        for profile in ['Vision', 'XHTTP', 'Fallback', 'Trojan', 'mKCP', 'SNI']:
            with self.subTest(profile=profile):
                config = json.loads(json.dumps(self.initial))
                config['xray'].update(
                    tag=profile, port=8443,
                    uuid='00000000-0000-4000-8000-000000000001',
                    fallback='00000000-0000-4000-8000-000000000002',
                    trojan='public-test-fixture-not-for-use', kcp='public-test-fixture',
                    path='/public-test-fixture', privateKey=fixture_key,
                    target='example.com', serverNames=['example.com'], shortIds=['0123456789abcdef'],
                )
                config['xray']['rules']['reset'] = 0
                self.config_file.write_text(json.dumps(config))
                generated = self.home / f'{profile}.json'
                result = self.run_shell('handler_xray_config', prelude=f"""
SCRIPT_XRAY_DIR={shlex.quote(str(ROOT / 'config/xray'))}
XRAY_CONFIG_PATH={shlex.quote(str(generated))}
""")
                self.assert_success(result)
                data = json.loads(generated.read_text())
                # Validation does not bind listeners; avoid host log paths entirely.
                data['log'] = {'loglevel': 'warning'}
                generated.write_text(json.dumps(data))
                env = {**self.env, 'XRAY_LOCATION_ASSET': os.environ.get('XRAY_LOCATION_ASSET', str(self.binary.parent))}
                result = subprocess.run(
                    [str(self.binary), 'run', '-test', '-config', str(generated)],
                    cwd=self.home, env=env, capture_output=True, text=True, timeout=20,
                )
                self.assert_success(result)
                self.assertIn('Configuration OK', result.stdout + result.stderr)


class NginxSocketPermissionTests(ShellCase):
    def setUp(self):
        super().setUp()
        self.unit = self.home / 'nginx.service'
        self.sockets = self.home / 'sockets'
        self.initial['xray']['tag'] = 'SNI'
        self.config_file.write_text(json.dumps(self.initial))

    def repair(self, *, group_exists=True):
        # Relocate the helper's two literal host paths before evaluation; this
        # test can only read/change files below TemporaryDirectory().
        function = named_function(ROOT / 'core/handler.sh', 'repair_nginx_xray_socket_permissions')
        self.assertIn("'/etc/systemd/system/nginx.service'", function)
        function = function.replace('/etc/systemd/system/nginx.service', str(self.unit))
        function = function.replace('/dev/shm/nginx', str(self.sockets))
        return self.run_shell('repair_nginx_xray_socket_permissions', prelude=function + f"""
getent() {{ return {0 if group_exists else 1}; }}
systemctl() {{ printf 'systemctl %s\\n' "$*" >> "$CALLS_FILE"; }}
chgrp() {{ printf 'chgrp %s\\n' "$*" >> "$CALLS_FILE"; }}
chmod() {{ printf 'chmod %s\\n' "$*" >> "$CALLS_FILE"; }}
""")

    def old_unit(self):
        return f"""[Service]
ExecStartPre=/bin/mkdir {self.sockets}
ExecStartPre=/bin/chmod 711 {self.sockets}
ExecStopPost=/bin/rm -rf {self.sockets}
"""

    def test_managed_unit_migration_is_backed_up_and_idempotent(self):
        old = self.old_unit()
        self.unit.write_text(old)
        self.sockets.mkdir(mode=0o711)
        self.assert_success(self.repair())
        self.assertEqual(Path(str(self.unit) + '.before-xray-user.bak').read_text(), old)
        self.assertIn(f'ExecStartPre=-/bin/chgrp xray {self.sockets}', self.unit.read_text())
        self.assertIn(f'ExecStartPre=/bin/chmod 0771 {self.sockets}', self.unit.read_text())
        self.assertIn(f'chmod 0771 {self.sockets}', (self.home / 'calls').read_text())
        self.assertNotIn('chmod 0777', (self.home / 'calls').read_text())
        migrated = self.unit.read_text()
        self.assert_success(self.repair())
        self.assertEqual(self.unit.read_text(), migrated)
        self.assertEqual(Path(str(self.unit) + '.before-xray-user.bak').read_text(), old)
        self.assertEqual((self.home / 'calls').read_text().count('systemctl daemon-reload'), 1)

    def test_unknown_unit_is_not_modified(self):
        custom = '[Service]\n# user-managed service\n'
        self.unit.write_text(custom)
        self.sockets.mkdir(mode=0o711)
        self.assert_success(self.repair())
        self.assertEqual(self.unit.read_text(), custom)
        self.assertFalse((self.home / 'calls').exists())
        self.assertEqual(self.sockets.stat().st_mode & 0o777, 0o711)

    def test_missing_xray_group_does_not_change_old_installation(self):
        self.unit.write_text(self.old_unit())
        self.sockets.mkdir(mode=0o711)
        self.assert_success(self.repair(group_exists=False))
        self.assertEqual(self.unit.read_text(), self.old_unit())
        self.assertFalse((self.home / 'calls').exists())

    def test_runtime_symlink_is_not_chmodded(self):
        self.unit.write_text(self.old_unit())
        target = self.home / 'unrelated'
        target.mkdir(mode=0o700)
        self.sockets.symlink_to(target, target_is_directory=True)
        self.assert_success(self.repair())
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        self.assertNotIn('chgrp ', (self.home / 'calls').read_text())


if __name__ == "__main__":
    unittest.main()
