"""Read-only original CLI startup diagnosis; no credential, HTTP or release authority."""
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

SOURCE = '007c40b58304bdafc229fcaa08b89bc873609203'
HELPERS = {'publisher_publication_intake.py', 'remote_maven_readback.py', 'publication_policy.py',
           'github_publication_receipts.py', 'connected_publication_intake.py'}
CHILD = r'''import json, os, pathlib, re, runpy, sys
scripts = pathlib.Path(__file__).parent / 'scripts'
allowed = {str(scripts / name): name for name in (
    'publisher_publication_intake.py', 'remote_maven_readback.py', 'publication_policy.py',
    'github_publication_receipts.py', 'connected_publication_intake.py')}
permitted = {'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE'}

def names():
    assert len(os.environ) <= 128
    return sorted(key if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', key) else '<other>' for key in os.environ)

report = {'startup_env_names': names(), 'python_version': list(sys.version_info[:3]),
          'main_entered': False, 'native_boundary_entered': False, 'sites': [],
          'network_attempts_blocked': 0, 'subprocess_attempts_blocked': 0}

def audit(event, args):
    if event.startswith('socket.'):
        report['network_attempts_blocked'] += 1
        raise RuntimeError('Diagnostic network forbidden')
    if event in ('subprocess.Popen', 'os.system', 'os.posix_spawn'):
        report['subprocess_attempts_blocked'] += 1
        raise RuntimeError('Diagnostic subprocess forbidden')

sys.addaudithook(audit)
sys.path.insert(0, str(scripts))

def trace(frame, event, arg):
    file = allowed.get(frame.f_code.co_filename)
    function = frame.f_code.co_name
    if file == 'publisher_publication_intake.py' and event == 'call':
        if function == 'main':
            report['main_entered'] = True
            report['main_env_names'] = names()
            report['environment_matches_allowlist'] = set(os.environ) <= permitted
        elif function == 'native_boundary':
            report['native_boundary_entered'] = True
    if file and event == 'exception' and len(report['sites']) < 32:
        kind = arg[0].__name__
        if kind not in ('StopIteration', 'GeneratorExit', 'SystemExit'):
            report['sites'].append({'file': file, 'function': function, 'line': frame.f_lineno,
                'kind': kind if kind in ('Refusal', 'AssertionError', 'TypeError', 'ValueError',
                'ImportError', 'ModuleNotFoundError', 'AttributeError', 'RuntimeError', 'OSError') else 'other'})
    return trace

code = None
sys.settrace(trace)
try:
    # run_path preserves the original CLI's empty argument list and original bytes.
    runpy.run_path(str(scripts / 'publisher_publication_intake.py'), run_name='__main__')
except SystemExit as error:
    code = error.code if type(error.code) is int else None
except BaseException:
    pass  # No exception text, traceback, arbitrary filenames or values are emitted.
finally:
    sys.settrace(None)
report['original_exit_code'] = code
schema = any(site['file'] == 'publisher_publication_intake.py' and site['line'] in (335, 336) for site in report['sites'])
if code == 1 and report['main_entered'] and not report['network_attempts_blocked'] and not report['subprocess_attempts_blocked']:
    if not report['environment_matches_allowlist'] and not report['native_boundary_entered']:
        report['result'] = 'original-main-environment-refused'
    elif report['environment_matches_allowlist'] and report['native_boundary_entered'] and schema:
        report['result'] = 'original-main-reached-invalid-request-schema'
report.setdefault('result', 'unexpected-diagnostic-outcome')
data = json.dumps(report, sort_keys=True) + '\n'
assert len(data.encode()) <= 32768
sys.stdout.write(data)
sys.exit(1 if report['result'] == 'unexpected-diagnostic-outcome' else 0)
'''


def main():
    os.umask(0o077)
    source = (Path(os.environ['GITHUB_WORKSPACE']) / 'producer').resolve(strict=True)
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    root = Path(os.environ['KIRA_BOUNDARY_ROOT'])
    owner = {key: os.environ[key] for key in ('GITHUB_REPOSITORY', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA')}
    # The workflow supplies no credentials; discard all ambient inputs before subprocesses.
    os.environ.clear()
    os.environ.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', LANG='C', LC_ALL='C')
    home = None
    report = {'scope': 'original CLI startup with invalid request; NOT native/release authority',
              'source_sha': SOURCE, 'phase': 'owned-inputs'}
    try:
        assert platform.system() == 'Darwin' and platform.machine() == 'arm64'
        assert owner['GITHUB_REPOSITORY'] == 'kira-manga/kira-source-engine'
        assert all(re.fullmatch(r'[1-9][0-9]*', owner[key]) for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))
        assert root == temp / ('kira-boundary-diagnostic-' + owner['GITHUB_RUN_ID'] + '-' + owner['GITHUB_RUN_ATTEMPT'])
        assert not any(path.is_symlink() for path in (root, *root.parents)) and root.is_dir()
        assert root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o077 == 0
        marker = root / 'owner.json'
        assert not marker.is_symlink() and marker.is_file() and marker.stat().st_size <= 4096
        assert json.loads(marker.read_text()) == owner
        home = Path(tempfile.mkdtemp(prefix='home-', dir=root))
        os.environ.update(HOME=str(home), TMPDIR=str(home))
        report['phase'] = 'source'
        git_env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                       GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0')
        assert subprocess.check_output(['/usr/bin/git', '-C', str(source), 'rev-parse', 'HEAD'],
                                       env=git_env, timeout=30).decode().strip() == SOURCE
        sys.path.insert(0, str(source / '.github/scripts'))
        import publication_policy as policy
        snapshot = policy.source_snapshot(source, SOURCE, fresh=True)
        report['phase'] = 'staged-helpers'
        scripts = home / 'scripts'
        scripts.mkdir(mode=0o700)
        report['helpers'] = {}
        for name in sorted(HELPERS):
            data = policy.read_small(source / '.github/scripts' / name)
            assert policy.digest(data) == snapshot['files']['.github/scripts/' + name]['sha256']
            with (scripts / name).open('xb') as output:
                output.write(data)
            report['helpers'][name] = policy.digest(data)
        probe = home / 'probe.py'
        probe.write_text(CHILD)
        # Exact five ProcessBuilder inputs. Do NOT clear the fresh child's startup environment.
        environment = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(home), 'TMPDIR': str(home),
                       'LANG': 'C', 'LC_ALL': 'C'}
        report['phase'] = 'original-cli-startup'
        result = subprocess.run(['/usr/bin/python3', '-B', '-E', '-s', str(probe)], input=b'{}',
                                cwd=source, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        assert len(result.stdout) <= 32768 and len(result.stderr) <= 2048
        child = json.loads(result.stdout)
        assert type(child) is dict and child.get('result') in (
            'original-main-environment-refused', 'original-main-reached-invalid-request-schema', 'unexpected-diagnostic-outcome')
        report['child'] = child
        report['stderr_was_original_fixed_refusal'] = result.stderr == b'Native publisher boundary refused. No release authority.\n'
        assert result.returncode == 0 and report['stderr_was_original_fixed_refusal']
        report['result'] = child['result']
    except Exception:
        report['result'] = 'diagnostic-preparation-or-execution-refused'
    finally:
        try:
            if home is not None:
                shutil.rmtree(home)
            report['owned_scratch_removed'] = home is None or not home.exists()
        except Exception:
            report['owned_scratch_removed'] = False
        data = json.dumps(report, indent=2) + '\n'
        assert len(data.encode()) <= 65536
        with (temp / 'kira-boundary-diagnostic.json').open('x') as output:
            output.write(data)
    return 0 if report['owned_scratch_removed'] and report.get('result', '').startswith('original-main-') else 1


if __name__ == '__main__':
    sys.exit(main())
