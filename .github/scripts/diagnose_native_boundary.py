"""One read-only historical-boundary diagnosis; never a publication/approval result."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

SOURCE = '9af6800394af9f666463dc58ae675c293e5780a2'
HELPERS = {'publisher_publication_intake.py', 'remote_maven_readback.py', 'publication_policy.py',
           'github_publication_receipts.py', 'connected_publication_intake.py'}


def main():
    os.umask(0o077)
    package_token = os.environ.pop('KIRA_PACKAGES_READ_TOKEN', '')
    assert platform.system() == 'Darwin' and platform.machine() == 'arm64'
    source = (Path(os.environ['GITHUB_WORKSPACE']) / 'producer').resolve(strict=True)
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    home = Path(tempfile.mkdtemp(prefix='kira-readonly-boundary-', dir=temp))
    report = {'scope': 'read-only historical boundary diagnosis; NOT release authority', 'source_sha': SOURCE,
              'phase': 'source', 'exceptions': [], 'http': [], 'logical_reads': 0}
    # No credential/ambient Git options may enter even the initial source inspection.
    os.environ.clear()
    os.environ.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', HOME=str(home), TMPDIR=str(home), LANG='C', LC_ALL='C')
    try:
        git_env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                       GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0')
        assert subprocess.check_output(['/usr/bin/git', '-C', str(source), 'rev-parse', 'HEAD'],
                                       env=git_env, timeout=30).decode().strip() == SOURCE
        sys.path.insert(0, str(source / '.github/scripts'))
        import remote_maven_readback as remote
        import github_publication_receipts as receipts
        # Import only exact original helpers; no edits, synthetic approval or Gradle execution.
        remote.policy.source_snapshot(source, SOURCE, fresh=True)
        report['phase'] = 'read-account'
        account = receipts._get_json('/user', package_token)
        username = account['login']
        remote.read_credentials(username, package_token)
        allowed = {str(source / '.github/scripts' / name): name for name in HELPERS}
        def trace(frame, event, arg):
            name = allowed.get(frame.f_code.co_filename)
            if name and event == 'exception' and len(report['exceptions']) < 64:
                kind = arg[0].__name__
                if kind not in ('StopIteration', 'GeneratorExit'):
                    report['exceptions'].append({'file': name, 'line': frame.f_lineno,
                                                 'kind': kind if kind in ('Refusal', 'AssertionError', 'KeyError', 'TypeError',
                                                 'ValueError', 'OSError', 'TimeoutError', 'SSLCertVerificationError',
                                                 'ConnectionError', 'FileNotFoundError') else 'other'})
            # HTTP status only. Never error text, request/response payloads, URLs or headers.
            if name == 'remote_maven_readback.py' and frame.f_code.co_name == '_read' and event == 'call':
                report['logical_reads'] += 1
            if name == 'remote_maven_readback.py' and event == 'line' and frame.f_code.co_name == '_read':
                response = frame.f_locals.get('response')
                if response is not None and isinstance(response.status, int):
                    value = {'request': report['logical_reads'], 'status': response.status}
                    if len(report['http']) < 64 and value not in report['http']:
                        report['http'].append(value)
            return trace
        report['phase'] = 'first-authenticated-maven-index'
        sys.settrace(trace)
        try:
            observed = remote._read_index('source-contract-android', (username, package_token), remote._Budget())
            report['index'] = {key: observed[key] for key in ('size', 'sha256')}
            report['result'] = 'index-and-four-sidecars-matched; NOT whole boundary or release acceptance'
        except Exception:
            report['result'] = 'refused; inspect fixed source lines, no cause guessed'
        finally:
            sys.settrace(None)
    except Exception:
        report['result'] = 'diagnostic-preparation-refused'
    finally:
        package_token = None
        shutil.rmtree(home)
        report['owned_scratch_removed'] = not home.exists()
        # Only fixed projections survive; artifact/source inputs are not re-uploaded.
        (temp / 'kira-boundary-diagnostic.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report.get('result', '').startswith(('refused;', 'index-and-')) else 1


if __name__ == '__main__':
    sys.exit(main())
