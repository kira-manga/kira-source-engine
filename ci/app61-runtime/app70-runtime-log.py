#!/usr/bin/env python3
"""One installed-runtime log loading/query check; no app, canary or privacy proof.

Reuses the accepted App8 command owner and App5 log/cleanup allowance unchanged.
No runtime download, framework copying, private override, stream or host-log fallback.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import os
from pathlib import Path
import platform
import pwd
import re
import secrets
import shutil
import signal
import stat
import sys
import time

XCODE = '/Applications/Xcode_26.4.1.app/Contents/Developer'
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-26-4'
DEVICE_TYPE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-17'
OWNER_SHA = '9f69c87182eba29030fcd65c49b4e8459a41749440dab9767f3fbfe0c02db6cf'
COMMANDS_SHA = '4134f5dcc39aa7b7a182bf45701dc5192e7e5c0f4ff07065a948cb2c2b84acc1'
UUID = r'[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}'


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and path.resolve() == path, 'Noncanonical directory: ' + str(path))
    return [info.st_dev, info.st_ino, info.st_uid]


def fingerprint(path, end, maximum=16777216):
    require(time.monotonic() < end, 'File identity deadline expired')
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and path.resolve() == path and 0 < before.st_size <= maximum,
            'Missing, aliased or oversized runtime/control file: ' + str(path))
    with path.open('rb') as stream:
        data = stream.read(maximum + 1)
    after = path.lstat()
    fields = ('st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    require(len(data) == before.st_size and all(getattr(before, key) == getattr(after, key) for key in fields)
            and time.monotonic() < end, 'File changed or exceeded its identity deadline')
    return {'path': str(path), 'device': before.st_dev, 'inode': before.st_ino, 'uid': before.st_uid,
            'mode': before.st_mode, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def load(path, expected, name, end):
    require(fingerprint(path, end)['sha256'] == expected, 'Changed accepted support file: ' + path.name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call(c, args, label, seconds=15, cleaning=False, end=None):
    return c['commands'].call(['/usr/bin/xcrun', *args], label, seconds=seconds, cleaning=cleaning,
        end=end if end is not None else (c['commands'].end if cleaning else c['workEnd']))


def devices(c, cleaning=False):
    rows = c['owner'].parse_json(call(c, ['simctl', 'list', 'devices', '--json'], 'devices', cleaning=cleaning))
    values = [dict(row, runtime=runtime) for runtime, entries in rows['devices'].items() for row in entries]
    require(len(values) <= 512, 'Oversized device inventory')
    return values


def save_state(c):
    c['owner'].save(c['run'] / 'reports/simulator.json', c['simulator'])


def simulator_identity(c, row):
    state = c['simulator']
    require(re.fullmatch(UUID, state['udid']) and
            (row['udid'], row['name'], row['runtime'], row['deviceTypeIdentifier']) ==
            (state['udid'], state['name'], RUNTIME, DEVICE_TYPE), 'Different owned simulator inventory')
    root = c['home'] / 'Library/Developer/CoreSimulator/Devices' / state['udid']
    identity = directory(root)
    require(identity[2] == os.getuid() and row['dataPath'] == str(root / 'data')
            and directory(root / 'data')[2] == os.getuid(), 'Different owned simulator root/data path')
    return {'udid': state['udid'], 'root': str(root), 'identity': identity, 'dataPath': row['dataPath']}


def select_runtime_log(c):
    require(call(c, ['xcodebuild', '-version'], 'xcode-version').strip() ==
            'Xcode 26.4.1\nBuild version 17E202', 'Different installed Xcode; no installation/fallback')
    runtimes = c['owner'].parse_json(call(c, ['simctl', 'list', 'runtimes', '--json'], 'runtimes'))['runtimes']
    matches = [row for row in runtimes if row['identifier'] == RUNTIME and row.get('isAvailable') is True]
    require(len(matches) == 1 and matches[0]['version'] == '26.4.1' and matches[0]['buildversion'] == '23E254a'
            and 'arm64' in matches[0]['supportedArchitectures'], 'Missing exact installed runtime; no download/fallback')
    row = matches[0]
    root = Path(row['runtimeRoot'])
    require(root.is_absolute() and root.name == 'RuntimeRoot', 'Missing exact inventory runtimeRoot; no host fallback')
    root_identity = directory(root)
    tool = root / 'usr/bin/log'
    proof = {'runtime': row, 'rootIdentity': root_identity}
    c['runtimeLog'] = proof
    c['owner'].save(c['run'] / 'reports/runtime-log.json', proof)
    proof['toolBefore'] = fingerprint(tool, c['workEnd'])
    require(proof['toolBefore']['mode'] & 0o111, 'Runtime log is not executable')
    proof['architectures'] = call(c, ['lipo', '-archs', str(tool)], 'runtime-log-architectures').strip()
    require('arm64' in proof['architectures'].split(), 'Runtime log lacks ARM64 Mach-O')
    proof['macho'] = call(c, ['vtool', '-arch', 'arm64', '-show-build', str(tool)], 'runtime-log-platform')
    require(re.findall(r'(?m)^\s*platform\s+(\S+)\s*$', proof['macho']) == ['IOSSIMULATOR'],
            'Runtime log is not an iOS Simulator Mach-O; no host fallback')
    c['owner'].save(c['run'] / 'reports/runtime-log.json', proof)
    return tool


def create_simulator(c):
    state = c['simulator']
    types = c['owner'].parse_json(call(c, ['simctl', 'list', 'devicetypes', '--json'], 'device-types'))['devicetypes']
    require(sum(row['identifier'] == DEVICE_TYPE for row in types) == 1, 'Missing installed iPhone17 type')
    require(not any(row['name'] == state['name'] for row in devices(c)), 'Owned simulator name already exists')
    state['creating'] = True
    save_state(c)  # Intent precedes the only simulator creation, including failed command joins.
    state['udid'] = call(c, ['simctl', 'create', state['name'], DEVICE_TYPE, RUNTIME], 'create-owned-simulator').strip()
    require(re.fullmatch(UUID, state['udid']), 'Invalid created simulator UUID')
    state['creating'] = False
    matches = [row for row in devices(c) if row['udid'] == state['udid'] or row['name'] == state['name']]
    require(len(matches) == 1 and matches[0]['state'] == 'Shutdown', 'Created simulator is not uniquely shut down')
    state['bound'] = simulator_identity(c, matches[0])
    state['bootIntended'] = True
    save_state(c)
    boot_end = min(c['workEnd'], time.monotonic() + 150)
    call(c, ['simctl', 'boot', state['udid']], 'boot-owned-simulator', end=boot_end)
    call(c, ['simctl', 'bootstatus', state['udid'], '-b'], 'owned-bootstatus', seconds=150, end=boot_end)
    matches = [row for row in devices(c) if row['udid'] == state['udid'] or row['name'] == state['name']]
    require(len(matches) == 1 and matches[0]['state'] == 'Booted'
            and simulator_identity(c, matches[0]) == state['bound'], 'Owned simulator readiness/identity changed')


def dispose_simulator(c):
    state = c['simulator']
    if not state['creating'] and not state['udid']:
        return True
    matches = [row for row in devices(c, True) if row['name'] == state['name'] or row['udid'] == state['udid']]
    require(len(matches) <= 1, 'Ambiguous owned simulator; no deletion')
    if matches:
        row = matches[0]
        require(not state['udid'] or state['udid'] == row['udid'], 'Different disposal UUID')
        state['udid'] = row['udid']  # Recover only a unique post-intent name/runtime/type if create join failed.
        bound = simulator_identity(c, row)
        require(state.get('bound', bound) == bound, 'Owned simulator root changed before disposal')
        state['bound'], state['creating'] = bound, False
        save_state(c)
        if row['state'] != 'Shutdown':
            require(state['bootIntended'], 'Unowned boot state; retain simulator')
            call(c, ['simctl', 'shutdown', state['udid']], 'shutdown-owned-simulator', seconds=20, cleaning=True)
        rows = [row for row in devices(c, True) if row['udid'] == state['udid'] or row['name'] == state['name']]
        require(len(rows) == 1 and rows[0]['state'] == 'Shutdown' and simulator_identity(c, rows[0]) == bound,
                'Owned shutdown/root identity unproven; no deletion')
        call(c, ['simctl', 'delete', state['udid']], 'delete-owned-simulator', seconds=20, cleaning=True)
    require(not any(row['name'] == state['name'] or row['udid'] == state['udid'] for row in devices(c, True)),
            'Owned simulator remains in inventory')
    if state.get('bound'):
        require(not os.path.lexists(state['bound']['root']), 'Owned simulator root remains')
    state['deleted'] = True
    save_state(c)
    return True


def retain(c, public):
    require(directory(c['run']) == c['runIdentity'] and
            directory(c['run'] / 'reports') == c['reportsIdentity'], 'Changed owned evidence root')
    files = sorted((c['run'] / 'reports').iterdir())
    require(len(files) <= 192 and all(stat.S_ISREG(path.lstat().st_mode) and path.suffix in ('.log', '.json') for path in files)
            and sum(path.stat().st_size for path in files) <= 6291456, 'Evidence file/type/total cap exceeded')
    manifest = {}
    for path in files:
        require(path.stat().st_size <= 1048576 and time.monotonic() < c['commands'].end, 'Evidence size/deadline exceeded')
        with path.open('rb') as stream:
            data = stream.read(1048577)
        require(len(data) == path.stat().st_size and len(data) <= 1048576, 'Evidence changed or exceeded cap')
        with (public / path.name).open('xb') as output:
            output.write(data)
        manifest[path.name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    c['owner'].save(public / 'files.json', manifest)


def main():
    require(sys.argv[1:] == ['--tool-only'] and sys.flags.isolated and sys.dont_write_bytecode,
            'Only isolated, no-bytecode tool-only invocation is allowed')
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
            and os.environ.get('GITHUB_REPOSITORY') == 'kira-manga/kira-source-engine'
            and os.environ.get('GITHUB_REF') == 'refs/heads/validation/app61-runtime-inputs-20260915-01'
            and os.environ.get('GITHUB_EVENT_NAME') == 'push' and os.environ.get('GITHUB_RUN_ATTEMPT') == '1'
            and platform.system() == 'Darwin' and platform.machine() == 'arm64' and os.getuid() > 0,
            'Wrong hosted one-attempt macOS ARM invocation')
    run_id = os.environ['GITHUB_RUN_ID']
    require(re.fullmatch(r'[1-9][0-9]*', run_id), 'Invalid run identity')
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    source = Path(__file__).resolve()
    require(source == workspace / 'control/ci/app61-runtime/app70-runtime-log.py', 'Wrong carrier checkout')
    started = time.monotonic()
    end, work_end = started + 390, started + 240
    owner = load(source.with_name('app8-owner.py'), OWNER_SHA, 'app70_owner', work_end)
    adapter = load(source.with_name('app5-commands.py'), COMMANDS_SHA, 'app70_commands', work_end)
    owner.deadline_capabilities()  # Use installed hosted Python; never install/retry another interpreter.
    os.umask(0o077)
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, owner.interrupted)
    home = Path(os.environ['HOME']).resolve()
    require(home == Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(), 'Wrong real CoreSimulator account HOME')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    directory(temporary)
    run = temporary / ('app70-runtime-log-' + run_id + '-1')
    public = workspace / 'app70-reports'
    public.mkdir(mode=0o700)
    run.mkdir(mode=0o700)
    run_identity = directory(run)
    for child in ('reports', 'work'):
        (run / child).mkdir(mode=0o700)
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(home), 'CFFIXED_USER_HOME': str(home),
           'DEVELOPER_DIR': XCODE, 'LANG': 'C', 'LC_ALL': 'C', 'TZ': 'UTC'}
    commands = adapter.commands(owner, run, env, end)
    state = {'name': 'App70-Log-' + run_id + '-1-' + secrets.token_hex(8), 'creating': False,
             'udid': None, 'bootIntended': False, 'runtime': RUNTIME, 'deviceType': DEVICE_TYPE}
    c = {'owner': owner, 'commands': commands, 'run': run, 'home': home, 'simulator': state,
         'end': end, 'workEnd': work_end, 'runIdentity': run_identity, 'reportsIdentity': directory(run / 'reports')}
    result = {'schema': 'app70-runtime-log-capability-v1', 'capability': 'NOT_ESTABLISHED', 'errors': [],
              'carrierSha': os.environ['GITHUB_SHA'], 'runId': run_id, 'attempt': 1,
              'source': fingerprint(source, work_end), 'ownerSha256': OWNER_SHA, 'commandsSha256': COMMANDS_SHA,
              'python': sys.version, 'pythonExecutable': str(Path(sys.executable).resolve()),
              'limitsSeconds': {'work': 240, 'cleanup': 120, 'controller': 390, 'boot': 150, 'query': 20},
              'scope': 'installed runtime log Mach-O loading and one false-predicate query only',
              'appBuilds': 0, 'canaryStimuli': 0, 'tests': 0, 'privacyStatus': 'NOT_ASSESSED'}
    cleanup = {'commandsAbsent': False, 'workersAbsent': False, 'simulatorRemoved': False,
               'scratchRemoved': False, 'evidenceRetained': False, 'errors': []}
    try:
        result['stage'] = 'runtime-log-identity'
        tool = select_runtime_log(c)
        result['stage'] = 'owned-simulator'
        create_simulator(c)
        result['stage'] = 'runtime-log-query'
        proof = c['runtimeLog']
        require(directory(Path(proof['runtime']['runtimeRoot'])) == proof['rootIdentity']
                and fingerprint(tool, work_end) == proof['toolBefore'], 'Runtime log identity changed before query')
        upper = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=1)
        lower = upper - timedelta(seconds=1)
        predicate = 'FALSEPREDICATE AND (process == "' + state['name'] + '")'
        query = ['simctl', 'spawn', state['udid'], str(tool), 'show', '--style', 'json', '--timezone', 'UTC',
                 '--start', lower.strftime('%Y-%m-%d %H:%M:%S'), '--end', upper.strftime('%Y-%m-%d %H:%M:%S'),
                 '--info', '--debug', '--predicate', predicate]
        owner.save(run / 'reports/query.json', {'argv': ['/usr/bin/xcrun', *query], 'device': state['bound'],
            'startUtc': lower.isoformat(), 'endUtc': upper.isoformat(), 'privacyEvidence': False,
            'emptyResultWouldNotProvePrivacy': True, 'attempts': 1})
        call(c, query, 'runtime-log-query', seconds=20)  # Exactly one finite read-only query; no help/stream/retry.
        proof['toolAfter'] = fingerprint(tool, work_end)
        require(proof['toolAfter'] == proof['toolBefore'], 'Runtime log changed during query')
        owner.save(run / 'reports/runtime-log.json', proof)
        result['capability'] = 'AVAILABLE_TOOL_ONLY'
    except Exception as error:
        result['errors'].append(str(error)[:500])
    finally:
        commands.begin_cleanup(min(end - 15, time.monotonic() + 120))
        try:
            require(commands.drain(), 'Work command ownership unresolved; retain device/scratch')
            cleanup['simulatorRemoved'] = dispose_simulator(c)
        except Exception as error:
            cleanup['errors'].append('device: ' + str(error)[:500])
        try:
            commands.drain()
            observation = commands.observe(cleaning=True, full=True)
            markers = [str(run) + '/', state['name']] + ([state['udid']] if state['udid'] else [])
            workers = [row for row in observation['rows'] if row['pid'] != os.getpid()
                and not row['state'].startswith('Z') and any(marker in row['command'] for marker in markers)]
            cleanup['workers'] = [{key: row[key] for key in ('pid', 'uid', 'ppid', 'pgid', 'state')} for row in workers[:32]]
            cleanup['commandsAbsent'] = commands.drain(observation)
            commands.fresh(observation)
            cleanup['workersAbsent'] = not workers
            require(cleanup['commandsAbsent'] and cleanup['workersAbsent'], 'Owned workers remain; no global signaling')
        except Exception as error:
            cleanup['errors'].append('absence: ' + str(error)[:500])
        cleanup['forced'] = any(task['receipt']['forced'] for task in commands.tasks)
        cleanup['normalCommands'] = all(task['receipt']['normalJoin'] for task in commands.tasks[commands.cleanup_at:])
        cleanup['logsWithinCap'] = commands.within_cap() and not commands.log_budget_failed
        try:
            require(cleanup['commandsAbsent'] and cleanup['workersAbsent'], 'Cannot retain/remove live scratch')
            owner.save(run / 'reports/simulator.json', state)
            commands.checkpoint()
            retain(c, public)
            cleanup['evidenceRetained'] = True
            require(cleanup['simulatorRemoved'] and directory(run) == run_identity, 'Scratch ownership/cleanup unresolved')
            shutil.rmtree(run)
            cleanup['scratchRemoved'] = not os.path.lexists(run)
        except Exception as error:
            cleanup['errors'].append('evidence/scratch: ' + str(error)[:500])
        cleanup['withinDeadline'] = time.monotonic() < commands.end
        barrier = (all(cleanup[key] for key in ('commandsAbsent', 'workersAbsent', 'simulatorRemoved',
                   'scratchRemoved', 'evidenceRetained', 'normalCommands', 'logsWithinCap', 'withinDeadline'))
                   and not cleanup['forced'] and not cleanup['errors'] and not commands.audit_failed)
        passed = (result['capability'] == 'AVAILABLE_TOOL_ONLY' and barrier and commands.normal()
                  and not owner.CANCELLED and time.monotonic() < end)
        result.update(passed=passed, cleanup=cleanup, cleanupBarrier=barrier, cancelled=owner.CANCELLED,
                      elapsedSeconds=time.monotonic() - started,
                      retentionScope='bounded-reports' if cleanup['evidenceRetained'] else 'partial-or-summary-only')
        owner.save(public / 'result.json', result)
        retained = list(public.iterdir())  # Children never receive this public directory as a write target.
        require(len(retained) <= 194 and all(stat.S_ISREG(path.lstat().st_mode) and path.suffix in ('.json', '.log')
                and path.stat().st_size <= 1048576 for path in retained)
                and sum(path.stat().st_size for path in retained) <= 8388608, 'Unsafe public snapshot')
        output_path = Path(os.environ['GITHUB_OUTPUT'])
        require(output_path.is_file() and not output_path.is_symlink(), 'Invalid workflow output file')
        with output_path.open('a') as output:
            output.write('cleanup_barrier_absent=' + str(barrier).lower() + '\nretention_ready=true\n'
                         + 'capability=' + result['capability'] + '\n')
    return 0 if passed else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('App70 tool-only capability failed: ' + str(error)[:500], file=sys.stderr)
        raise SystemExit(1)
