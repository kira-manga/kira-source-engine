"""Read only the original first-writer HTTP prefixes; never release authority."""
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
VERSION = '0.2.3'
SELECTED = {
    'archive_sha256': '7b464f746905015663e8495ef5955a1d904bbe4ba8ce3fec476454b9664cbed7',
    'inventory_sha256': 'ee51226c4bdbd0682b679c939d21b69cd7886d15bc5d515739ac878f433c7985',
    'seal_sha256': '3faf0ccf2a205dc74d355abd094493faacce03de0aa85b7553e10ba3cd4eb861',
}
HELPERS = {'publisher_publication_intake.py', 'remote_maven_readback.py', 'publication_policy.py',
           'github_publication_receipts.py', 'connected_publication_intake.py'}


def main():
    os.umask(0o077)
    package_token = os.environ.pop('KIRA_PACKAGES_READ_TOKEN', '')
    source = (Path(os.environ['GITHUB_WORKSPACE']) / 'producer').resolve(strict=True)
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    root = Path(os.environ['KIRA_BOUNDARY_ROOT'])
    owner = {key: os.environ[key] for key in ('GITHUB_REPOSITORY', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA')}
    # No credential/ambient Git options enter even the initial source inspection.
    os.environ.clear()
    os.environ.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', LANG='C', LC_ALL='C')
    home = None
    report = {'scope': 'read-only HTTP prefixes; NOT whole boundary or release authority',
              'source_sha': SOURCE, 'version': VERSION, 'producer_run': 39190022581, 'artifact_id': 12159789360,
              'phase': 'owned-inputs', 'exceptions': [], 'http': [], 'indexes': [], 'logical_reads': 0}
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
        import remote_maven_readback as remote
        import github_publication_receipts as receipts
        snapshot = remote.policy.source_snapshot(source, SOURCE, fresh=True)
        assert remote.policy.committed_version(remote.policy.read_small(source / 'gradle.properties')) == VERSION
        report['helpers'] = {name: snapshot['files']['.github/scripts/' + name]['sha256'] for name in sorted(HELPERS)}
        report['phase'] = 'original-archive-intake'
        directory = root / 'artifact'
        archive = directory / 'kira-publication-bytes.tar'
        assert not directory.is_symlink() and directory.is_dir() and list(directory.iterdir()) == [archive]
        measured, metadata = remote.policy.archive_intake(archive, VERSION, snapshot['files'])
        assert measured == SELECTED
        inventory = metadata['inventory.tsv']
        publications = remote.cohort(inventory, VERSION)
        assert len(publications) == 15 and len(remote.policy.text_lines(inventory)) == 345
        report['intake'] = measured
        # Project only validated identity/kind/ordinal, never raw request targets or payloads.
        paths, artifacts = {}, {}
        for identity, item in sorted(publications.items()):
            artifacts[item['artifact']] = identity
            index = remote.policy.GROUP.replace('.', '/') + '/' + item['artifact'] + '/maven-metadata.xml'
            paths[index] = {'identity': identity, 'kind': 'index'}
            for algorithm in remote.policy.ALGORITHMS:
                paths[index + '.' + algorithm] = {'identity': identity, 'kind': algorithm}
            for ordinal, (path, _, _) in enumerate(item['rows'], 1):
                paths[path] = {'identity': identity, 'kind': 'candidate', 'ordinal': ordinal}
        assert len(paths) == 420
        allowed = {str(source / '.github/scripts' / name): name for name in HELPERS}
        lines = (source / '.github/scripts/remote_maven_readback.py').read_text().splitlines()
        response_lines = [number + 1 for number, line in enumerate(lines, 1)
                          if line.strip() == 'response = connection.getresponse()']
        assert len(response_lines) == 1

        def trace(frame, event, arg):
            name = allowed.get(frame.f_code.co_filename)
            function = frame.f_code.co_name
            if name and event == 'exception' and len(report['exceptions']) < 64:
                kind = arg[0].__name__
                if kind not in ('StopIteration', 'GeneratorExit'):
                    report['exceptions'].append({'file': name, 'function': function, 'line': frame.f_lineno,
                                                 'kind': kind if kind in ('Refusal', 'AssertionError', 'KeyError', 'TypeError',
                                                 'ValueError', 'OSError', 'TimeoutError', 'SSLCertVerificationError',
                                                 'ConnectionError', 'FileNotFoundError') else 'other'})
            if name != 'remote_maven_readback.py':
                return trace
            if event == 'exception' and function == '_children':
                element = frame.f_locals['element']
                tags = {'metadata', 'groupId', 'artifactId', 'versioning', 'latest', 'release', 'versions', 'version', 'lastUpdated'}
                report['structure'] = {'parent': element.tag if element.tag in tags else 'other',
                                       'child_count': len(element), 'attribute_count': len(element.attrib),
                                       'children': [child.tag if child.tag in tags else 'other' for child in list(element)[:32]]}
            if event == 'return' and function == '_read_index' and type(arg) is dict:
                assert len(report['indexes']) < 15
                report['indexes'].append({'identity': artifacts[frame.f_locals['artifact']],
                                          'size': arg['size'], 'sha256': arg['sha256'],
                                          'version_count': len(arg['versions']), 'release_present': arg['release'] is not None,
                                          'candidate_absent': VERSION not in arg['versions']})
            if function == '_read' and event == 'call':
                assert frame.f_locals['path'] in paths and report['logical_reads'] < 420
                report['logical_reads'] += 1
                report['current_request'] = {'request': report['logical_reads'], **paths[frame.f_locals['path']]}
            # Record once immediately after getresponse, not stale responses on later loop lines.
            if function == '_read' and event == 'line' and frame.f_lineno == response_lines[0]:
                response, hop = frame.f_locals['response'], frame.f_locals['hop']
                assert type(response.status) is int and 100 <= response.status <= 599 and hop in (0, 1)
                assert len(report['http']) < 840
                report['http'].append({**report['current_request'], 'hop': hop, 'status': response.status})
            return trace

        report['phase'] = 'read-account'
        account = receipts._get_json('/user', package_token)
        username = account['login']
        remote.read_credentials(username, package_token)
        report['account_admitted'] = True
        report['phase'] = 'all-indexes'
        sys.settrace(trace)
        try:
            # Each original function supplies its own unchanged shared 300-second cohort budget.
            indexes = remote.prewrite_indexes(inventory, VERSION, username=username, token=package_token)
            assert set(indexes) == set(publications) and report['logical_reads'] == 75
            report['indexes_passed'] = True
            report['phase'] = 'all-candidate-paths'
            remote.refuse_existing_paths(inventory, VERSION, username=username, token=package_token)
            assert report['logical_reads'] == 420
            report['candidate_paths_passed'] = True
            report['result'] = 'both-prefixes-passed; later native boundary remains unobserved'
        except Exception:
            report['result'] = 'refused; inspect fixed source lines, no historical cause inferred'
        finally:
            sys.settrace(None)
    except Exception:
        report['result'] = 'diagnostic-preparation-refused'
    finally:
        sys.settrace(None)
        package_token = None
        try:
            if home is not None:
                shutil.rmtree(home)
            report['owned_scratch_removed'] = home is None or not home.exists()
        except Exception:
            report['owned_scratch_removed'] = False
        # The workflow always removes the owned downloaded archive; only this projection is uploaded.
        data = json.dumps(report, indent=2) + '\n'
        assert len(data.encode()) <= 256 * 1024
        with (temp / 'kira-boundary-diagnostic.json').open('x') as output:
            output.write(data)
    return 0 if report['owned_scratch_removed'] and report.get('result', '').startswith(('refused;', 'both-prefixes-')) else 1


if __name__ == '__main__':
    sys.exit(main())
