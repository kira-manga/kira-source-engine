"""PRIVATE UNEXECUTED App8 Apple draft; never a shipping app build.

Definitions only on import. The fixed command-group/census/device recipe is a
small adaptation of the reviewed Darwin owner, not an imported Engine5 runner.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time

ISSUE = 'cebc952602fb69e03eb654dab30937e2f830a239'
BASE = '2d6bf4bb9a67773abbf9c6c6bf641ffbd1de670c'
MANIFEST = '881dfc6e82754f3082d868a65f157dee54e4620fb1b23f084cbe998cf5a42bcf'
GUARD_HASH = 'c33add8f6fc647a9186e5e06affb433a174077601c4fb04c10ca4ef7bc2841a6'
BUILD_HASH = '1068ab565071844513c06e1cfa7b8580bc4d9278dd2a8661aac903c45082533c'
POLICY_PATH = 'iosApp/iosApp/Info.plist'
POLICIES = {'allow': '708ca4944a642ef0304608f3fe7615563d0ec703b453256169157951d836c50a',
            'deny': 'c0c2f75b51066e4ebc3b1e62296be449ba731ac299b6c7cd117130ba335224df'}
INPUTS = ('ios/TransportProbe.swift', 'fixture/no_forward_proxy.py', 'policy-inputs/allow/Info.plist',
          'policy-inputs/deny/Info.plist', 'policy-inputs.json', 'provenance.json')
XCODE = '/Applications/Xcode_26.4.1.app/Contents/Developer'
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-26-4'
DEVICE_TYPE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-17'
PF_HASH = '675449d18fdca83c4e892b719215832092f8362d193fe9c72cd230918b5d150b'
PF_RULES = 'set skip on lo0\nblock drop quick all\n'
PF = ['/usr/bin/sudo', '-n', '/sbin/pfctl']
PS = ['/bin/ps', '-axww', '-o', 'pid=,uid=,ppid=,pgid=,stat=,comm=,args=']
GROUP_PS = ['/bin/ps', '-axww', '-o', 'pid=,uid=,ppid=,pgid=,stat=']
PACKAGE, EXECUTABLE = 'me.manga.kira.transportprobe', 'App8TransportProbe'
HOSTS = ('raijinscan.co', 'app8-probe.raijinscan.co')
OLD_ATS = {'NSExceptionDomains': {'raijinscan.co': {
    'NSExceptionAllowsInsecureHTTPLoads': True, 'NSIncludesSubdomains': True}}}
CANCELLED = False


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def interrupted(_number, _frame):
    global CANCELLED
    CANCELLED = True  # Never raise across Popen creation/registration or owned cleanup.


def digest(path):
    require(path.is_file() and not path.is_symlink(), 'Missing/nonregular bound file: ' + str(path))
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            value.update(block)
    return value.hexdigest()


def parse_json(text):
    require(len(text.encode()) <= 1048576, 'Oversized JSON')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    def constant(_value):
        raise RuntimeError('Non-finite JSON')
    return json.loads(text, object_pairs_hook=unique, parse_constant=constant)


def read_json(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 131072, 'Invalid JSON receipt/input')
    return parse_json(path.read_text())


def save(path, value, limit=131072):
    data = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    require(len(data) <= limit and not path.is_symlink(), 'Invalid/oversized receipt')
    pending = path.with_name(path.name + '.pending')
    with pending.open('xb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    pending.replace(path)


def deadline_capabilities():
    require(all(hasattr(os, name) for name in ('waitid', 'P_PID', 'WEXITED', 'WNOHANG', 'WNOWAIT', 'CLD_EXITED', 'CLD_KILLED', 'CLD_DUMPED', 'killpg')),
            'Installed Python lacks Darwin non-reaping ownership APIs; no installation/fallback')
    require(signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL, 'Automatic child reaping would lose group ownership')


class Commands:
    """Finite local argv recipe: unreaped leaders pin groups through all signals.

    Only the two exact ps projections are trusted leaves. All-UID observations
    are local to one barrier; whole-host argv remains private scratch.
    """
    def __init__(self, run, env, end):
        self.run, self.reports, self.env, self.end = run, run / 'reports', env, end
        self.tasks, self.audit_failed, self.observer = [], False, None
        self.epoch, self.progress, self.failed_observer_progress = 0, 0, None
        self.owner = {'pid': os.getpid(), 'realUid': os.getuid(), 'effectiveUid': os.geteuid()}

    def checkpoint(self):
        try:
            save(self.reports / 'commands.json', [task['receipt'] for task in self.tasks], limit=1048576)
        except Exception:
            self.audit_failed = True
            raise

    def within_cap(self):
        sizes = [task['log'].stat().st_size for task in self.tasks if task['log'].exists()]
        return not sizes or max(sizes) <= 1048576 and sum(sizes) <= 4194304

    def start(self, argv, label, seconds=30, end=None, extra=None, cleaning=False, launch_by=None):
        argv = list(map(str, argv))
        limit = min(self.end, time.monotonic() + seconds, self.end if end is None else end)
        require((cleaning or not CANCELLED) and time.monotonic() < limit, 'Cancelled/expired before child launch')
        require(cleaning or self.within_cap(), 'Output cap exceeded before child launch')
        leaf = argv in (PS, GROUP_PS)
        require(not leaf or self.observer is None or self.observer['process'] is None
                or self.observer['receipt']['leaderReaped'], 'Prior observer is still owned/unreaped')
        log = (self.run / 'work' if leaf else self.reports) / f'{len(self.tasks) + 1:03d}-{label}.log'
        receipt = {'argv': argv, 'label': label, 'pid': None, 'deadline': limit,
                   'started': time.monotonic(), 'actualExit': None, 'leaderReaped': False,
                   'groupQuiet': False, 'forced': False, 'timedOut': False, 'ownershipLost': False,
                   'signalAttempts': [], 'errors': [], 'normalJoin': False, 'owner': self.owner,
                   'output': 'private census; not retained' if leaf else log.name}
        task = {'process': None, 'log': log, 'receipt': receipt, 'leaf': leaf, 'signalingClosed': False, 'identity': None}
        self.epoch += 1  # Every launch intent invalidates older observations, including failed launches.
        self.tasks.append(task)
        if leaf:
            self.observer = task  # Register even a child whose launch/checkpoint subsequently fails.
        try:
            self.checkpoint()
            with log.open('xb') as output:
                receipt['launched'] = time.monotonic()
                require((cleaning or not CANCELLED) and receipt['launched'] < limit
                        and (launch_by is None or receipt['launched'] <= launch_by), 'Launch/window deadline expired')
                task['process'] = subprocess.Popen(argv, env=dict(self.env, **(extra or {})), cwd=self.env['HOME'],
                                                   stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                                   close_fds=True, start_new_session=True)
                receipt['pid'] = task['process'].pid
                task['identity'] = {'pid': receipt['pid'], 'ppid': self.owner['pid'], 'pgid': receipt['pid']}
                receipt['identity'] = task['identity']
                if not leaf:
                    self.progress += 1
            receipt['spawnReturned'] = time.monotonic()
            self.checkpoint()
            require((cleaning or not CANCELLED) and receipt['spawnReturned'] < limit
                    and (launch_by is None or receipt['spawnReturned'] <= launch_by), 'Child creation missed its launch cap')
            return task
        except Exception as error:
            self.failed(task, error)
            raise

    def failed(self, task, error):
        receipt = task['receipt']
        receipt['normalJoin'] = False
        receipt['timedOut'] |= time.monotonic() >= min(receipt['deadline'], self.end)
        receipt['errors'].append(str(error)[:500])

    def peek(self, task):
        receipt, process = task['receipt'], task['process']
        require(process is not None and not receipt['leaderReaped'] and not receipt['ownershipLost'],
                'Missing/reaped/lost leader; refuse recycled-PGID signaling')
        try:
            info = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            receipt['ownershipLost'] = True
            raise RuntimeError('Leader unexpectedly reaped; refuse recycled-PGID signaling')
        if info is not None:
            if (task['identity'] != {'pid': process.pid, 'ppid': self.owner['pid'], 'pgid': process.pid}
                    or receipt['pid'] != process.pid or info.si_pid != process.pid
                    or info.si_code not in (os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED)):
                receipt['ownershipLost'] = True
                raise RuntimeError('Non-reaping exit identity/status mismatch')
            if not task['leaf'] and 'lastExitObservation' not in receipt:
                self.progress += 1  # Real target exit, never observer creation/reaping, permits a retry.
            receipt['lastExitObservation'] = {'pid': info.si_pid, 'uid': info.si_uid, 'code': info.si_code,
                                              'status': info.si_status, 'at': time.monotonic()}
        return info

    def await_exit(self, task, cleaning=False, limit=None):
        limit = min(self.end, task['receipt']['deadline'] if limit is None else limit)
        while time.monotonic() < limit and (cleaning or not CANCELLED):
            require(cleaning or self.within_cap(), 'Diagnostic output cap exceeded')
            info = self.peek(task)
            if info is not None:
                return info
            time.sleep(0.025)
        task['receipt']['timedOut'] |= time.monotonic() >= limit
        raise RuntimeError('Child cancelled or exceeded its cap: ' + task['receipt']['label'])

    def reap(self, task, info, end=None):
        receipt, process = task['receipt'], task['process']
        require(info is not None and info.si_pid == process.pid and not receipt['ownershipLost']
                and not receipt['leaderReaped'], 'No matching owned exit to settle')
        require(task['leaf'] or (receipt['groupQuiet'] and receipt.get('groupObservationEpoch') == self.epoch),
                'Non-leaf exit lacks fresh group proof')
        limit = min(self.end, self.end if end is None else end)
        require(time.monotonic() < limit, 'No bounded reap window; retain fence')
        task['signalingClosed'] = True
        receipt['actualExit'] = process.wait(timeout=min(2, max(0, limit - time.monotonic())))
        receipt['leaderReaped'], receipt['groupQuiet'] = True, True
        receipt['ended'] = time.monotonic()
        # Lifetime settlement alone never changes normalJoin, prior failures or timeouts.

    def output(self, task, cleaning=False):
        receipt = task['receipt']
        require(receipt['leaderReaped'] and receipt['groupQuiet'] and receipt['actualExit'] == 0
                and receipt['ended'] < min(receipt['deadline'], self.end) and (cleaning or not CANCELLED)
                and not receipt['timedOut'] and not receipt['forced'] and not receipt['errors'],
                'Child failed, cancelled or exceeded its cap: ' + receipt['label'])
        require(cleaning or self.within_cap(), 'Diagnostic output cap exceeded at child exit')
        require(task['log'].stat().st_size <= 1048576, 'Oversized tool output')
        raw = task['log'].read_text(errors='strict' if task['leaf'] else 'replace')
        receipt['normalJoin'] = True
        return raw

    def fresh(self, observation):
        require(observation is not None and observation['owner'] is self and observation['epoch'] == self.epoch
                and observation['observer']['receipt']['normalJoin'] and not self.audit_failed,
                'Missing/stale/failed process observation')

    def retain_malformed_groups(self, task, sequence):
        receipt, source = task['receipt'], task['log']
        def in_time():
            require(time.monotonic() < min(receipt['deadline'], self.end), 'Malformed group census retention deadline exceeded')
        def identity(path):
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode), 'Nonregular malformed group census log')
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
        in_time()
        destination = self.reports / f'{sequence:03d}-malformed-owned-groups.log'
        require(task['leaf'] and receipt['argv'] == GROUP_PS and receipt['leaderReaped']
                and source == self.run / 'work' / f'{sequence:03d}-owned-groups.log'
                and self.reports == self.run / 'reports'
                and all(path.is_dir() and not path.is_symlink()
                        for path in (self.run, source.parent, self.reports)),
                'Invalid malformed group census retention path')
        before = identity(source)
        receipt['outputBytesAtLastRead'] = before[2]
        require(before[2] <= 1048576 and self.within_cap(), 'Malformed group census retention cap exceeded')
        in_time()
        with source.open('rb') as stream:
            in_time()
            data = stream.read(1048577)
        in_time()
        require(identity(source) == before and len(data) == before[2]
                and self.within_cap(), 'Malformed group census log changed or exceeded cap')
        sha256 = hashlib.sha256(data).hexdigest()
        in_time()
        require(not destination.exists() and not destination.is_symlink(), 'Malformed group census destination exists')
        in_time()
        os.link(source, destination, follow_symlinks=False)  # Exclusive: a raced destination cannot be overwritten.
        task['log'] = destination  # Account for retained bytes even if scratch unlink fails.
        receipt['output'] = destination.name
        receipt['censusRetention'] = {'status': 'LINKED', 'name': destination.name, 'scratchRemoved': False}
        in_time()
        require(identity(destination) == before and self.within_cap(), 'Linked malformed group census changed or exceeded cap')
        in_time()
        with destination.open('rb') as stream:
            in_time()
            linked_data = stream.read(1048577)
        in_time()
        linked_sha256 = hashlib.sha256(linked_data).hexdigest()
        in_time()
        require(identity(destination) == before and len(linked_data) == before[2]
                and linked_sha256 == sha256 and self.within_cap(), 'Linked malformed group census bytes changed or exceeded cap')
        require(identity(source) == before, 'Malformed group census scratch identity changed before deletion')
        in_time()
        receipt['censusRetention'].update(bytesAtRetention=len(linked_data), sha256AtRetention=linked_sha256)
        source.unlink()
        receipt['censusRetention']['scratchRemoved'] = True
        in_time()
        require(identity(destination) == before and self.within_cap(), 'Retained malformed group census changed or exceeded cap')
        in_time()
        receipt['censusRetention']['status'] = 'RETAINED'

    def observe(self, cleaning=False, full=False, end=None):
        require(self.failed_observer_progress != self.progress, 'Observer failed without relevant target progress')
        before = len(self.tasks)
        self.failed_observer_progress = self.progress  # Clear only after a wholly successful observer/receipt.
        task = None
        try:
            task = self.start(PS if full else GROUP_PS, 'owned-census' if full else 'owned-groups',
                              seconds=10 if end is None else max(0, end - time.monotonic()), end=end, cleaning=cleaning)
            self.reap(task, self.await_exit(task, cleaning), end=task['receipt']['deadline'])
            raw = self.output(task, cleaning)
            require(raw.endswith('\n') and raw.strip(), 'Empty/truncated owned-process census')
            rows, seen = [], set()
            for line_index, line in enumerate(raw.splitlines(), 1):
                fields = line.split(None, 6) if full else line.split()
                valid = ((len(fields) in (6, 7) if full else len(fields) == 5)
                         and all(re.fullmatch(r'[0-9]+', value) for value in fields[:4])
                         and re.fullmatch(r'[IRSTUZ?][<AELNOSTVWXs+>]*', fields[4]))
                if not valid:
                    predicate = 'FIELD_COUNT'
                    if (len(fields) in (6, 7) if full else len(fields) == 5):
                        predicate = next((name for name, value in zip(
                            ('PID_DIGITS', 'UID_DIGITS', 'PPID_DIGITS', 'PGID_DIGITS'), fields[:4])
                            if not re.fullmatch(r'[0-9]+', value)), 'STAT')
                    task['receipt']['censusParseFailure'] = {'lineIndex': line_index, 'fieldCount': len(fields),
                                                           'predicate': predicate}
                require(valid, 'Malformed owned-process census')
                row = dict(zip(('pid', 'uid', 'ppid', 'pgid'), map(int, fields[:4])), state=fields[4])
                require(row['pid'] not in seen, 'Duplicate process census identity')
                seen.add(row['pid'])
                if full:
                    row.update(executable=Path(fields[5]).name,
                               command=fields[5] + ' ' + (fields[6] if len(fields) == 7 else ''))
                rows.append(row)
            require(len(rows) <= 8192, 'Oversized process census')
            require(any(all(row[key] == value for key, value in task['identity'].items()) for row in rows),
                    'Missing/mismatched observer identity in census')
            require(time.monotonic() < task['receipt']['deadline'] and self.within_cap(),
                    'Late/oversized process observation')
            task['receipt']['outputBytesAtLastRead'] = task['log'].stat().st_size
            task['log'].unlink()
            self.checkpoint()
            require(time.monotonic() < task['receipt']['deadline'], 'Observer receipt exceeded its cap')
        except Exception as error:
            self.failed_observer_progress = self.progress
            if len(self.tasks) > before:
                task = self.tasks[before]
                self.failed(task, error)
                if (task['receipt']['argv'] == GROUP_PS and task['receipt']['leaderReaped']
                        and 'censusParseFailure' in task['receipt']):
                    try:
                        self.retain_malformed_groups(task, before + 1)
                    except Exception:
                        task['receipt'].setdefault('censusRetention', {})['status'] = 'FAILED'
                        task['receipt']['errors'].append('Malformed group census retention failed')
                elif task['log'].exists():
                    task['receipt']['outputBytesAtLastRead'] = task['log'].stat().st_size
                    if task['receipt']['leaderReaped']:
                        task['log'].unlink()
                try:
                    self.checkpoint()
                except Exception:
                    pass
            raise
        self.failed_observer_progress = None
        return {'owner': self, 'epoch': self.epoch, 'rows': rows, 'full': full, 'observer': task, 'drained': False}

    def census(self, cleaning=False):
        return self.observe(cleaning=cleaning, full=True)['rows']

    def group_quiet(self, task, observation):
        receipt = task['receipt']
        receipt['groupQuiet'] = False  # A failed fresh barrier cannot leave an old true value.
        self.fresh(observation)
        require(not receipt['leaderReaped'] and not receipt['ownershipLost'] and task['identity'] is not None,
                'No pinned group identity')
        rows = [row for row in observation['rows'] if row['pgid'] == task['identity']['pgid']]
        receipt['lastGroup'] = [{key: row[key] for key in ('pid', 'uid', 'ppid', 'pgid', 'state')} for row in rows[:32]]
        receipt['groupObserverPid'] = observation['observer']['receipt']['pid']
        require(len(rows) <= 32, 'Unexpectedly large owned tool group')
        if not any(all(row[key] == value for key, value in task['identity'].items()) for row in rows):
            receipt['ownershipLost'] = True
            raise RuntimeError('Pinned leader omitted/mismatched in all-UID group observation')
        receipt['groupObservationEpoch'] = self.epoch
        receipt['groupQuiet'] = all(row['state'].startswith('Z') for row in rows)
        return receipt['groupQuiet']

    def wait(self, task, cleaning=False):
        receipt = task['receipt']
        try:
            info = self.await_exit(task, cleaning)
            limit = min(receipt['deadline'], self.end)
            while time.monotonic() < limit and (cleaning or not CANCELLED):
                if task['leaf'] or self.group_quiet(task, self.observe(cleaning=cleaning, end=limit)):
                    self.reap(task, info, end=limit)
                    return self.output(task, cleaning)
                time.sleep(0.025)
            receipt['timedOut'] |= time.monotonic() >= limit
            raise RuntimeError('Group cancelled or exceeded its cap: ' + receipt['label'])
        except Exception as error:
            self.failed(task, error)
            raise
        finally:
            if task['log'].exists():
                receipt['outputBytesAtLastRead'] = task['log'].stat().st_size
            self.checkpoint()

    def call(self, argv, label, seconds=30, end=None, extra=None, cleaning=False):
        return self.wait(self.start(argv, label, seconds, end, extra, cleaning), cleaning)

    def force(self, task, observation=None):
        receipt, process = task['receipt'], task['process']
        if process is None or receipt['leaderReaped']:
            return
        try:
            receipt['timedOut'] |= time.monotonic() >= receipt['deadline']
            info = self.peek(task)
            if info is not None and (task['leaf'] or (observation is not None and self.group_quiet(task, observation))):
                self.reap(task, info)  # No signals, observer or 12s reservation for an already-resolved leaf/group.
                return
            require(not task['signalingClosed'] and not receipt['ownershipLost'], 'No safe owned group signal remains')
            require(time.monotonic() + 12 < self.end, 'No bounded TERM/KILL/reap window; retain fence')
            receipt['forced'] = True
            receipt['signalAttempts'] = [{'signal': sig.name, 'outcome': 'planned'} for sig in (signal.SIGTERM, signal.SIGKILL)]
            try:
                self.checkpoint()
            except Exception:
                pass  # Failed receipts never abandon an already-owned group.
            try:
                for index, sig in enumerate((signal.SIGTERM, signal.SIGKILL)):
                    self.peek(task)  # Retain/match the unreaped child through every permitted signal.
                    self.epoch += 1
                    receipt['groupQuiet'] = False
                    try:
                        os.killpg(process.pid, sig)
                        receipt['signalAttempts'][index]['outcome'] = 'sent'
                        if not task['leaf']:
                            self.progress += 1
                    except ProcessLookupError:
                        receipt['signalAttempts'][index]['outcome'] = 'alreadyGone'
                    except OSError as error:
                        receipt['signalAttempts'][index]['outcome'] = str(error)[:500]
                        receipt['errors'].append('Group signal failed')
                    if index == 0:
                        time.sleep(10)  # Existing fixed grace, only for unresolved live/unknown work.
            finally:
                task['signalingClosed'] = True
            if task['leaf']:
                end = min(self.end, time.monotonic() + 2)
                self.reap(task, self.await_exit(task, cleaning=True, limit=end), end=end)
            # Non-leaves remain pinned: drain obtains one shared post-signal observation before reaping.
        finally:
            self.checkpoint()

    def retire_observer(self):
        task = self.observer
        if task is not None and task['process'] is not None and not task['receipt']['leaderReaped']:
            try:
                self.force(task)
            except Exception as error:
                self.failed(task, error)

    def settle_groups(self, pending, observation):
        for task in pending:
            if task['receipt']['leaderReaped']:
                continue
            try:
                info = self.peek(task)
                quiet = self.group_quiet(task, observation)
                if info is not None and quiet:
                    self.reap(task, info)
                    if not task['receipt']['forced']:
                        self.output(task, cleaning=True)  # Preserves any earlier failure, even after lifetime settlement.
            except Exception as error:
                self.failed(task, error)

    def drain(self, observation=None):
        if observation is not None:
            self.fresh(observation)
            require(not observation['drained'], 'Process observation already consumed by another drain barrier')
            observation['drained'] = True
        self.retire_observer()  # No observer creation here, and no recursive wait/census cleanup.
        pending = [task for task in self.tasks if not task['leaf'] and task['process'] is not None
                   and not task['receipt']['leaderReaped']]
        for task in pending:
            if not task['receipt']['errors'] and not task['receipt']['forced']:
                try:
                    self.await_exit(task, cleaning=True)  # Fixtures retain their original natural 30s bound.
                except Exception as error:
                    self.failed(task, error)
        if pending:
            try:
                if observation is None:
                    observation = self.observe(cleaning=True)
                self.settle_groups(pending, observation)
            except Exception as error:
                for task in pending:
                    self.failed(task, error)
            self.retire_observer()
            before_signals = self.epoch
            for task in pending:
                if not task['receipt']['leaderReaped'] and not task['signalingClosed']:
                    try:
                        self.force(task)
                    except Exception as error:
                        self.failed(task, error)
            if self.epoch != before_signals:
                try:
                    self.settle_groups(pending, self.observe(cleaning=True))
                except Exception as error:
                    for task in pending:
                        if not task['receipt']['leaderReaped']:
                            self.failed(task, error)
                self.retire_observer()  # Settle a failed final observer, but never retry it in a loop.
        self.checkpoint()
        return all(task['process'] is None or (task['receipt']['leaderReaped'] and task['receipt']['groupQuiet']
                   and not task['receipt']['ownershipLost']) for task in self.tasks)

    def normal(self):
        return not self.audit_failed and all(task['receipt']['normalJoin'] and not task['receipt']['forced']
            and not task['receipt']['timedOut'] and not task['receipt']['errors'] for task in self.tasks)


def pf_lines(text):
    warnings = {'No ALTQ support in kernel', 'ALTQ related functions disabled'}
    return [line.strip() for line in text.splitlines() if line.strip() and line.strip() not in warnings]


def pf_enabled(text):
    rows = [line for line in pf_lines(text) if line.startswith('Status:')]
    require(len(rows) == 1, 'Missing/ambiguous PF status')
    match = re.match(r'Status:\s+(Enabled|Disabled)(?:\s|$)', rows[0])
    require(match is not None, 'Unsupported PF status')
    return match[1] == 'Enabled'


def owned_token(text):
    rows = pf_lines(text)
    tokens = [re.fullmatch(r'Token\s*:\s*([1-9][0-9]*)', row) for row in rows]
    tokens = [match[1] for match in tokens if match]
    require(len(tokens) == 1 and 0 < int(tokens[0]) < 2 ** 64, 'Missing/ambiguous owned PF token; retain fence')
    return tokens[0]


def sole_reference(text, token=None):
    rows = pf_lines(text)
    if token is None:
        require(rows == ['No pf starter references held'], 'PF references/unknown owner present')
        return
    require(len(rows) == 2 and re.fullmatch(r'PID\s+(?:Process\s+Name|NAME)\s+TOKEN\s+TIMESTAMP', rows[0], re.I),
            'Unknown PF reference-list shape; retain fence')
    match = re.fullmatch(r'([1-9][0-9]*)\s+pfctl\s+([1-9][0-9]*)\s+(.+)', rows[1])
    require(match is not None and match[2] == token, 'PF reference is not the sole persisted owned token')


def interface_skips(text):
    result = {}
    for row in pf_lines(text):
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9]*)( \(skip\))?', row)
        require(match is not None and match[1] not in result, 'Unknown/duplicate PF interface/skip output')
        result[match[1]] = bool(match[2])
    require('ALL' in result and 'lo0' in result, 'Incomplete PF interface view')
    return result


def ordinary_interfaces(text, route4, route6):
    require(not re.search(r'MANAGEMENT|INTCOPROC|COPROCESSOR', text, re.I), 'PF-bypass/management interface; STOP')
    parts = re.split(r'(?m)^(?=[A-Za-z][A-Za-z0-9]*: flags=)', text)
    require(not parts[0].strip(), 'Unknown interface inventory prefix')
    names, active = set(), set()
    for block in parts[1:]:
        header = re.match(r'([A-Za-z][A-Za-z0-9]*): flags=[0-9a-f]+<([^>]*)> mtu [0-9]+ index [0-9]+\n', block)
        require(header is not None and header[1] not in names, 'Unknown/duplicate interface header')
        name, flags = header[1], set(header[2].split(','))
        require(re.fullmatch(r'lo0|en[01]|anpi0|gif0|stf0|XHC12|utun[0-3]', name), 'Unsupported interface: ' + name)
        names.add(name)
        if re.search(r'(?m)^\s+inet6? ', block):
            require(name == 'lo0' or name == 'en0' or re.fullmatch(r'utun[0-3]', name), 'Unexpected IP-bearing interface')
            active.add(name)
        if name == 'lo0':
            require({'UP', 'LOOPBACK', 'RUNNING'} <= flags and '\tinet 127.0.0.1 ' in block and '\tinet6 ::1 ' in block,
                    'Missing ordinary numeric loopback')
        elif name == 'en0':
            require('type: Ethernet\n' in block and 'status: active\n' in block, 'Unexpected active Ethernet profile')
        elif name.startswith('utun'):
            require({'UP', 'POINTOPOINT', 'RUNNING'} <= flags, 'Unknown IPv6 tunnel profile')
    require({'lo0', 'en0'} <= active, 'Missing ordinary loopback/Ethernet IP path')
    for family, text in ((4, route4), (6, route6)):
        rows = [line.strip() for line in text.splitlines() if line.strip()]
        require(len(rows) >= 3 and rows[:2] == ['Routing tables', 'Internet:' if family == 4 else 'Internet6:']
                and rows[2].split() == ['Destination', 'Gateway', 'Flags', 'Netif', 'Expire'], 'Unknown numeric route display')
        defaults = []
        for row in rows[3:]:
            fields = row.split()
            require(4 <= len(fields) <= 5 and fields[3] in active, 'Unknown/bypass route interface')
            if fields[0] == 'default':
                defaults.append(fields[3])
                require(fields[3] == 'en0' if family == 4 else bool(re.fullmatch(r'utun[0-3]', fields[3])) and 'I' in fields[2],
                        'Unexpected external default route profile')
        require(defaults == ['en0'] if family == 4 else len(defaults) == len(set(defaults)), 'Unknown default-route ownership')
    return names


def check_pf(snapshot, token=None):
    require(pf_enabled(snapshot['status']) is (token is not None), 'PF status changed/does not match the required phase')
    sole_reference(snapshot['references'], token)
    skips = interface_skips(snapshot['interfaces'])
    names = ordinary_interfaces(snapshot['ifconfig'], snapshot['ipv4'], snapshot['ipv6'])
    require(set(skips) == names | {'ALL'}, 'Unknown/missing PF interface relative to the ordinary interface view')
    skipped = {name for name, value in skips.items() if value}
    require(skipped == {'lo0'} if token else skipped <= {'lo0'}, 'External/unknown skip or missing active loopback skip')
    rules, nat, anchors = pf_lines(snapshot['rules']), pf_lines(snapshot['nat']), pf_lines(snapshot['anchors'])
    require(set(anchors) <= {'com.apple'} and len(anchors) == len(set(anchors)), 'Unknown PF anchor owner')
    if token is None:
        require((not rules and not nat and not anchors) or
                (rules == ['scrub-anchor "com.apple/*" all fragment reassemble', 'anchor "com.apple/*" all']
                 and nat == ['nat-anchor "com.apple/*" all', 'rdr-anchor "com.apple/*" all'] and anchors == ['com.apple']),
                'Unexpected inactive root PF configuration; no generic normalization')
    else:
        require(rules == ['block drop quick all'] and not nat, 'Incomplete root deny or applicable anchor/NAT path')
    require(not pf_lines(snapshot['states']), 'Nonempty PF state table')


def may_release(token_record, token, ownership_verified, absence):
    return (token is not None and token_record == {'token': token, 'policySha256': hashlib.sha256(PF_RULES.encode()).hexdigest()}
            and ownership_verified is True and all(absence.get(key) is True for key in
                ('nativeAbsent', 'fixturesAbsent', 'commandsAbsent', 'workersAbsent', 'simulatorRemoved', 'receiptSaved')))


def pf_snapshot(commands, label, cleaning=False):
    queries = {'status': ['-s', 'info'], 'references': ['-s', 'References'], 'rules': ['-s', 'rules'],
               'nat': ['-s', 'nat'], 'anchors': ['-s', 'Anchors'], 'states': ['-s', 'states'],
               'interfaces': ['-v', '-s', 'Interfaces']}
    result = {key: commands.call(PF + argv, label + '-' + key, seconds=5, cleaning=cleaning) for key, argv in queries.items()}
    for key, argv in (('ifconfig', ['/sbin/ifconfig', '-a', '-v']),
                      ('ipv4', ['/usr/sbin/netstat', '-rn', '-f', 'inet']),
                      ('ipv6', ['/usr/sbin/netstat', '-rn', '-f', 'inet6'])):
        result[key] = commands.call(argv, label + '-' + key, seconds=5, cleaning=cleaning)
    save(commands.reports / (label + '.json'), result)
    return result


def verify_owned_pf(commands, label, token, firewall, cleaning=False):
    require(not firewall['ownershipLost'], 'PF ownership previously lost; retain fence')
    try:
        check_pf(pf_snapshot(commands, label, cleaning), token)
    except Exception:
        firewall.update(ownershipVerified=False, ownershipLost=True)
        raise
    firewall['ownershipVerified'] = True


def bind_inputs(source, run, request):
    payload, inputs = source.parent.parent / 'docs/remediation/app8-native-apple02', run / 'inputs'
    require(digest(payload / 'manifest.json') == MANIFEST, 'Frozen native manifest mismatch')
    manifest = read_json(payload / 'manifest.json')
    entries = {row['path']: row for row in manifest['files']}
    require(len(entries) == len(manifest['files']), 'Duplicate frozen manifest path')
    for relative in INPUTS:
        original, target = payload / relative, inputs / relative
        require(original.stat().st_size == entries[relative]['bytes'] and digest(original) == entries[relative]['sha256'],
                'Frozen Apple/fixture/policy input mismatch')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        require(digest(target) == entries[relative]['sha256'], 'Copied frozen input changed')
    for phase in POLICIES:
        require(digest(inputs / f'policy-inputs/{phase}/Info.plist') == POLICIES[phase], 'Shipping plist-byte mismatch')
    metadata, provenance = read_json(inputs / 'policy-inputs.json'), read_json(inputs / 'provenance.json')
    builds = {row['path']: row['sha256'] for row in metadata['currentBuildInputsReadDirectly']}
    require(metadata['base'] == BASE and metadata['ios']['deploymentTarget'] == '15.0'
            and metadata['ios']['shippingPlistBinding'] == 'iosApp/Info.plist' and builds['iosApp/project.yml'] == BUILD_HASH
            and provenance['productBase'] == BASE and provenance['sourceGuardResultSha256'] == GUARD_HASH
            and metadata['productPatchSha256'] == provenance['productPatchSha256'], 'Frozen Apple provenance mismatch')
    path = payload / 'apple-checkpoint-policy-provenance.json'
    require(digest(path) == request['checkpointProvenanceSha256'], 'Missing/unbound primary Apple checkpoint provenance')
    checkpoint = {'schema': 'app8-apple-source-checkpoint-v1', **{key: request[key] for key in (
        'issueSha', 'historicalSha', 'shippingPolicyPath', 'policySha256', 'shippingBuildInputSha256',
        'probeManifestSha256', 'acceptedSourceGuardResultSha256')}}
    require(read_json(path) == checkpoint, 'Primary checkpoint does not attest the bound Apple policy bytes')
    save(run / 'reports/source-bindings.json', {'checkpoint': checkpoint, 'checkpointProvenanceSha256': digest(path),
         'frozenInputs': {relative: entries[relative] for relative in INPUTS}, 'sourceGuardRerun': False,
         'orchestratorSha256': digest(source), 'requestSha256': digest(source.with_name('app8-apple.request.json')),
         'pfDisposableAmendmentSha256': request['pfDisposableAmendmentSha256']})
    return inputs


def derived_plist(original):
    values = {'DEVELOPMENT_LANGUAGE': 'en', 'EXECUTABLE_NAME': EXECUTABLE, 'PRODUCT_BUNDLE_IDENTIFIER': PACKAGE,
              'PRODUCT_NAME': EXECUTABLE, 'PRODUCT_BUNDLE_PACKAGE_TYPE': 'APPL', 'MARKETING_VERSION': '1.0.5',
              'CURRENT_PROJECT_VERSION': '1', 'KIRA_APP_STORE_ID': '6792232678', 'KIRA_CRASH_DIAGNOSTICS_ENABLED': 'NO'}
    def resolve(value):
        if isinstance(value, dict):
            return {key: resolve(child) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve(child) for child in value]
        if isinstance(value, str) and '$(' in value:
            require(value.startswith('$(') and value.endswith(')') and value[2:-1] in values, 'Unknown plist placeholder')
            return values[value[2:-1]]
        return value
    result = resolve(original)
    result.update(MinimumOSVersion='15.0', CFBundleSupportedPlatforms=['iPhoneSimulator'],
                  DTPlatformName='iphonesimulator', UIDeviceFamily=[1, 2])
    ats = lambda obj: {key: value for key, value in obj.items() if key.startswith('NSAppTransportSecurity')}
    require(ats(original) == ats(result), 'Derived simulator plist altered ATS')
    return result


def inspect_macho(commands, path, label, end):
    require(commands.call(['/usr/bin/xcrun', 'lipo', '-archs', path], label + '-arch', end=end).strip() == 'arm64', 'Not ARM64 Mach-O')
    text = commands.call(['/usr/bin/xcrun', 'vtool', '-show-build', path], label + '-platform', end=end)
    for key, expected in (('platform', 'IOSSIMULATOR'), ('minos', '15.0'), ('sdk', '26.4')):
        require(re.findall(r'(?m)^\s*' + key + r'\s+(\S+)\s*$', text) == [expected], 'Wrong compiled platform/deployment/SDK')


def prepare_bundles(commands, inputs):
    tools = {'developerDir': XCODE, 'pfctlSha256': digest(Path('/sbin/pfctl')), 'python': sys.version,
             'pythonExecutable': str(Path(sys.executable).resolve()), 'kernel': platform.release(),
             'macOS': '26.6.2', 'imageVersion': os.environ.get('ImageVersion'), 'imageOS': os.environ.get('ImageOS')}
    tools['xcode'] = commands.call(['/usr/bin/xcrun', 'xcodebuild', '-version'], 'xcode-version').strip()
    require(tools['xcode'] == 'Xcode 26.4.1\nBuild version 17E202', 'Different installed Xcode; no resolver/install')
    tools['sdkVersion'] = commands.call(['/usr/bin/xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'], 'sdk-version').strip()
    require(tools['sdkVersion'] == '26.4', 'Wrong installed simulator SDK')
    sdk = Path(commands.call(['/usr/bin/xcrun', '--sdk', 'iphonesimulator', '--show-sdk-path'], 'sdk-path').strip()).resolve()
    require(sdk.is_dir() and sdk.is_relative_to(Path(XCODE)), 'SDK is not inside the fixed installed Xcode')
    tools['sdkPath'], tools['sdkSettingsSha256'] = str(sdk), digest(sdk / 'SDKSettings.plist')
    tools['sdkBuild'] = commands.call(['/usr/bin/xcrun', '--sdk', 'iphonesimulator', '--show-sdk-build-version'], 'sdk-build').strip()
    require(re.fullmatch(r'[0-9]+[A-Z][0-9]+[a-z]?', tools['sdkBuild']), 'Unsupported SDK build identity')
    tools['swift'] = commands.call(['/usr/bin/xcrun', '--sdk', 'iphonesimulator', 'swiftc', '--version'], 'swift-version')
    tool_paths = [Path(path).resolve() for path in ('/sbin/pfctl', '/usr/bin/xcrun', '/usr/bin/codesign', '/usr/bin/plutil',
                  XCODE + '/usr/bin/simctl', XCODE + '/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc', sys.executable)]
    tools['toolSha256'] = {str(path): digest(path) for path in tool_paths}
    save(commands.reports / 'tool-identities.json', tools)
    end = min(commands.end, time.monotonic() + 90)
    binary = commands.run / ('work/' + EXECUTABLE)
    commands.call(['/usr/bin/xcrun', '--sdk', 'iphonesimulator', 'swiftc', '-swift-version', '5', '-parse-as-library',
                   '-target', 'arm64-apple-ios15.0-simulator', '-sdk', sdk, '-framework', 'UIKit', '-framework', 'Foundation',
                   '-framework', 'CFNetwork', inputs / 'ios/TransportProbe.swift', '-o', binary], 'compile-once', end=end)
    inspect_macho(commands, binary, 'compiled', end)
    shared = digest(binary)
    bundles = {}
    for phase in ('allow', 'deny'):
        bundle = commands.run / f'work/{phase}/{EXECUTABLE}.app'
        bundle.mkdir(parents=True)
        original = plistlib.loads((inputs / f'policy-inputs/{phase}/Info.plist').read_bytes())
        derived = derived_plist(original)
        path = bundle / 'Info.plist'
        path.write_bytes(plistlib.dumps(derived, sort_keys=False))
        plist_hash = digest(path)
        shutil.copyfile(binary, bundle / EXECUTABLE)
        (bundle / EXECUTABLE).chmod(0o700)
        require(digest(bundle / EXECUTABLE) == shared, 'Policy packages did not share one compiled Mach-O')
        commands.call(['/usr/bin/plutil', '-lint', path], phase + '-plist', end=end)
        commands.call(['/usr/bin/codesign', '--force', '--sign', '-', bundle], phase + '-sign', end=end)
        commands.call(['/usr/bin/codesign', '--verify', '--strict', bundle], phase + '-verify-signature', end=end)
        require(digest(path) == plist_hash and plistlib.loads(path.read_bytes()) == derived, 'Signing altered the bound plist')
        inspect_macho(commands, bundle / EXECUTABLE, phase + '-signed', end)
        bundles[phase] = {'bundle': str(bundle), 'sourcePlistSha256': POLICIES[phase], 'derivedPlistSha256': plist_hash,
                          'preSignMachOSha256': shared, 'signedMachOSha256': digest(bundle / EXECUTABLE),
                          'changedKeys': sorted(key for key in original.keys() | derived.keys() if original.get(key) != derived.get(key))}
    save(commands.reports / 'package-bindings.json', bundles)
    return bundles


def devices(commands, cleaning=False):
    value = parse_json(commands.call(['/usr/bin/xcrun', 'simctl', 'list', 'devices', '--json'], 'devices', seconds=5, cleaning=cleaning))
    return [row for rows in value['devices'].values() for row in rows]


def create_simulator(commands, state):
    runtimes = parse_json(commands.call(['/usr/bin/xcrun', 'simctl', 'list', 'runtimes', '--json'], 'runtimes'))['runtimes']
    matches = [row for row in runtimes if row['identifier'] == RUNTIME and row.get('isAvailable') is True]
    require(len(matches) == 1 and matches[0]['version'] == '26.4.1' and matches[0]['buildversion'] == '23E254a'
            and 'arm64' in matches[0]['supportedArchitectures'], 'Missing exact installed iOS26.4.1 runtime; no download/fallback')
    types = parse_json(commands.call(['/usr/bin/xcrun', 'simctl', 'list', 'devicetypes', '--json'], 'device-types'))['devicetypes']
    require(sum(row['identifier'] == DEVICE_TYPE for row in types) == 1, 'Missing installed iPhone17 device type')
    require(not any(row['name'] == state['name'] for row in devices(commands)), 'Owned simulator name already exists')
    state.update(creating=True, runtime=RUNTIME, runtimeVersion='26.4.1', runtimeBuild='23E254a', deviceType=DEVICE_TYPE)
    save(commands.run / 'simulator.json', state)
    state['udid'] = commands.call(['/usr/bin/xcrun', 'simctl', 'create', state['name'], DEVICE_TYPE, RUNTIME], 'create-owned-simulator').strip()
    require(re.fullmatch(r'[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}', state['udid']), 'Invalid created simulator identity')
    state['creating'] = False
    save(commands.run / 'simulator.json', state)
    end = min(commands.end, time.monotonic() + 180)
    commands.call(['/usr/bin/xcrun', 'simctl', 'boot', state['udid']], 'boot-owned-simulator', end=end)
    commands.call(['/usr/bin/xcrun', 'simctl', 'bootstatus', state['udid'], '-b'], 'owned-bootstatus', seconds=180, end=end)
    matches = [row for row in devices(commands) if row['udid'] == state['udid']]
    require(len(matches) == 1 and matches[0]['name'] == state['name'] and matches[0]['state'] == 'Booted', 'Owned simulator not booted')
    expected = Path(commands.env['HOME']) / 'Library/Developer/CoreSimulator/Devices' / state['udid'] / 'data'
    require(Path(matches[0]['dataPath']).resolve() == expected.resolve() and expected.is_dir(), 'Unknown owned simulator data root')
    state['dataPath'] = str(expected.resolve())
    save(commands.run / 'simulator.json', state)
    save(commands.reports / 'simulator.json', state)


def app_inventory(commands, udid, label, cleaning=False):
    raw = commands.call(['/usr/bin/xcrun', 'simctl', 'listapps', udid], label + '-apps', seconds=5, cleaning=cleaning)
    path = commands.run / ('work/' + label + '-apps.plist')
    path.write_text(raw)
    value = parse_json(commands.call(['/usr/bin/plutil', '-convert', 'json', '-o', '-', path], label + '-apps-json', seconds=5, cleaning=cleaning))
    require(isinstance(value, dict), 'Unsupported simctl application inventory')
    return value


def owned_workers(commands, state, cleaning=False, observation=None):
    if observation is None:
        observation = commands.observe(cleaning=cleaning, full=True)
    commands.fresh(observation)
    require(observation['full'], 'Escaped-worker proof requires the full all-UID inventory')
    rows = []
    for row in observation['rows']:
        if row['pid'] == os.getpid() or row['state'].startswith('Z'):
            continue
        native = row['executable'] == EXECUTABLE
        marker = str(commands.run) + '/' in row['command']
        device = bool(state.get('udid')) and state['udid'] in row['command']
        if native or marker or device:
            # Observe unknown ownership, but never turn a global-name match into signal authority.
            row['basis'] = [name for name, yes in (('probe-executable', native), ('owned-run-path', marker), ('owned-device', device)) if yes]
            rows.append({key: value for key, value in row.items() if key != 'command'})
    require(len(rows) <= 32, 'Unexpectedly large owned-worker census')
    return rows


def absence_barrier(commands, state, cleanup, fixtures):
    # Clear before any fresh work; exceptions must not preserve cached release flags.
    keys = ('nativeAbsent', 'fixturesAbsent', 'commandsAbsent', 'workersAbsent', 'receiptSaved')
    cleanup.update(dict.fromkeys(keys, False))
    commands.drain()  # Settle cleanup-created work before proof; this result is not absence evidence.
    observation = commands.observe(cleaning=True, full=True)
    workers = owned_workers(commands, state, cleaning=True, observation=observation)
    commands_absent = commands.drain(observation)
    commands.fresh(observation)  # A drain launch/signal invalidates the preceding escaped-worker view.
    cleanup.update(workers=workers, nativeAbsent=not any(row['executable'] == EXECUTABLE for row in workers),
                   workersAbsent=not workers, commandsAbsent=commands_absent,
                   fixturesAbsent=all(task['receipt']['leaderReaped'] and task['receipt']['groupQuiet']
                                      and not task['receipt']['ownershipLost'] for task in fixtures),
                   receiptSaved=not commands.audit_failed)
    try:
        save(commands.reports / 'cleanup.json', cleanup)
    except Exception:
        cleanup['receiptSaved'] = False
        raise


def accept_phase(native, ready, receipt, phase, nonce, fixture_pid, started, ended):
    for record in (native, ready, receipt):
        require(record['phase'] == phase and record['nonce'] == nonce, 'Phase/nonce binding mismatch')
    port = ready['port']
    require(type(port) is int and 1 <= port <= 65535, 'Invalid owned fixture port')
    for record in (ready, receipt):
        require(record['pid'] == fixture_pid and record['bindHost'] == '127.0.0.1'
                and record['port'] == port and record['lifetimeSeconds'] == 25, 'Fixture ownership mismatch')
    require(native['ok'] is True and native['failure'] == '' and native['proxyHost'] == '127.0.0.1' and native['proxyPort'] == port
            and native['systemVersion'] == '26.4.1' and native['deploymentTarget'] == '15.0' and native['minimumIOS15RuntimeProven'] is False
            and native['loadedATS'] == (OLD_ATS if phase == 'allow' else None), 'Invalid native policy/platform result')
    for key in ('readyMonotonicSeconds', 'deadlineMonotonicSeconds'):
        require(math.isfinite(ready[key]) and ready[key] == receipt[key], 'Fixture clock binding mismatch')
    require(ready['readyMonotonicSeconds'] <= started <= ready['readyMonotonicSeconds'] + 2
            and 0 < ready['deadlineMonotonicSeconds'] - ready['readyMonotonicSeconds'] <= 25
            and started <= ended <= ready['deadlineMonotonicSeconds']
            and ready['deadlineMonotonicSeconds'] <= receipt['closedMonotonicSeconds'] <= ready['deadlineMonotonicSeconds'] + 5
            and 25 <= receipt['elapsedSeconds'] <= 30, 'Native call/window incomplete or out of bounds')
    require(receipt['fixtureOK'] is True and receipt['windowComplete'] is True and receipt['errors'] == [], 'Fixture did not finish normally')
    rows = native['observations']
    require(len(rows) == 2, 'Missing native observation')
    for host, row in zip(HOSTS, rows):
        require(row['url'] == f'http://{host}/{nonce}' and row['violation'] == '', 'Wrong native subject or metric/response violation')
        for metric in row['metrics']:
            require(not metric['remoteAddress'] or (metric['remoteAddress'] in ('127.0.0.1', '::ffff:127.0.0.1')
                    and metric['remotePort'] == port), 'Native metrics observed a non-fixture endpoint')
        if phase == 'allow':
            require(row['outcome'] == 'canned-response' and row['responseCode'] == 200 and row['responseURL'] == row['url']
                    and row['receivedBodyBytes'] == len(f'app8-no-forward {nonce} {host}\n') and row['errorDomain'] == '' and row['errorCode'] == 0
                    and len(row['metrics']) == 1 and row['metrics'][0]['proxy'] is True and row['metrics'][0]['networkLoad'] is True
                    and row['metrics'][0]['remoteAddress'] in ('127.0.0.1', '::ffff:127.0.0.1')
                    and row['metrics'][0]['remotePort'] == port, 'Historical native proxy positive control failed')
        else:
            require(row['outcome'] == 'native-ATS-policy-rejection' and row['responseCode'] is None and row['receivedBodyBytes'] == 0
                    and row['errorDomain'] == 'NSURLErrorDomain' and row['errorCode'] == -1022, 'Not the specific native ATS denial')
    connections = receipt['connections']
    require(len(connections) <= 8 and all(row['error'] is None and type(row['bytes']) is int and 0 <= row['bytes'] <= 8192 for row in connections)
            and receipt['receivedBytes'] == sum(row['bytes'] for row in connections), 'Incomplete receiver accounting')
    if phase == 'allow':
        require(len(connections) == 2 and sorted(row['host'] for row in connections) == sorted(HOSTS)
                and all(row['bytes'] > 0 and row['cannedResponseSent'] and row['peerEOF'] for row in connections), 'Missing historical wire positive')
    else:
        require(receipt['receivedBytes'] == 0, 'Candidate sent HTTP bytes')


def native_phase(commands, inputs, state, bundles, phase, nonce, fixtures):
    udid = state['udid']
    require(PACKAGE not in app_inventory(commands, udid, phase + '-before'), 'Probe package is not initially absent')
    require(not any(row['executable'] == EXECUTABLE for row in owned_workers(commands, state)), 'Pre-existing/unknown native probe')
    state['installIntended'] = True
    save(commands.run / 'simulator.json', state)
    commands.call(['/usr/bin/xcrun', 'simctl', 'install', udid, bundles[phase]['bundle']], phase + '-install')
    paths = {}
    for kind in ('app', 'data'):
        path = Path(commands.call(['/usr/bin/xcrun', 'simctl', 'get_app_container', udid, PACKAGE, kind], phase + '-container-' + kind).strip())
        require(path.is_absolute() and path.is_dir() and path.resolve().is_relative_to(Path(state['dataPath'])), 'Foreign app container')
        paths[kind] = path.resolve()
    require(digest(paths['app'] / 'Info.plist') == bundles[phase]['derivedPlistSha256']
            and digest(paths['app'] / EXECUTABLE) == bundles[phase]['signedMachOSha256'], 'Installed policy/code differs from bound package')
    result_path = paths['data'] / 'Documents/app8-result.json'
    require(not result_path.exists(), 'Stale native result/app data')
    state.update(phase=phase, resultPath=str(result_path))
    save(commands.run / 'simulator.json', state)
    save(commands.reports / (phase + '-installation.json'), {'udid': udid, 'package': PACKAGE,
         'appContainer': str(paths['app']), 'dataContainer': str(paths['data']), 'bindings': bundles[phase]})
    directory = commands.run / ('work/' + phase + '/fixture')
    fixture = commands.start([Path(sys.executable).resolve(), '-B', inputs / 'fixture/no_forward_proxy.py',
                              '--phase', phase, '--nonce', nonce, '--output', directory], phase + '-fixture', seconds=30)
    fixtures.append(fixture)
    ready_limit = min(commands.end, fixture['receipt']['launched'] + 2)
    while not (directory / 'ready.json').exists() and time.monotonic() < ready_limit:
        require(not CANCELLED and commands.peek(fixture) is None, 'Fixture failed/cancelled before readiness')
        time.sleep(0.01)
    require(time.monotonic() < ready_limit and (directory / 'ready.json').is_file(), 'Fixture readiness exceeded two seconds')
    ready = read_json(directory / 'ready.json')
    require(ready['phase'] == phase and ready['nonce'] == nonce and ready['pid'] == fixture['process'].pid
            and ready['bindHost'] == '127.0.0.1' and type(ready['port']) is int and 1 <= ready['port'] <= 65535
            and ready['lifetimeSeconds'] == 25, 'Unbound fixture readiness')
    task = commands.start(['/usr/bin/xcrun', 'simctl', 'launch', '--console', udid, PACKAGE], phase + '-native', seconds=20,
                          end=ready['deadlineMonotonicSeconds'], launch_by=ready['readyMonotonicSeconds'] + 2,
                          extra={'SIMCTL_CHILD_APP8_PHASE': phase, 'SIMCTL_CHILD_APP8_NONCE': nonce,
                                 'SIMCTL_CHILD_APP8_PORT': str(ready['port'])})
    output = commands.wait(task)
    native_rows = re.findall(r'(?m)^APP8_NATIVE_RESULT (\{[^\n]+\})\s*$', output)
    native_pids = re.findall(r'(?m)^' + re.escape(PACKAGE) + r':\s+([1-9][0-9]*)\s*$', output)
    require(len(native_rows) == 1 and len(native_pids) == 1, 'Missing/ambiguous native stdout/PID receipt')
    native = parse_json(native_rows[0])
    require(read_json(result_path) == native, 'Native stdout and owned data-container result disagree')
    commands.wait(fixture)
    receipt = read_json(directory / 'receipt.json')
    save(commands.reports / (phase + '-native.json'), {'result': native, 'printedNativePid': int(native_pids[0]),
         'nativeStart': task['receipt']['launched'], 'nativeEnd': task['receipt']['ended'], 'udid': udid})
    save(commands.reports / (phase + '-fixture.json'), {'ready': ready, 'receipt': receipt})
    accept_phase(native, ready, receipt, phase, nonce, fixture['process'].pid, task['receipt']['launched'], task['receipt']['ended'])
    require(not any(row['executable'] == EXECUTABLE for row in owned_workers(commands, state)), 'Native survived normal phase exit')
    commands.call(['/usr/bin/xcrun', 'simctl', 'uninstall', udid, PACKAGE], phase + '-uninstall')
    require(PACKAGE not in app_inventory(commands, udid, phase + '-after') and not paths['data'].exists(), 'Probe package/data survived uninstall')
    state['installIntended'] = False
    state.pop('resultPath', None)
    save(commands.run / 'simulator.json', state)


def dispose_simulator(commands, state, cleanup):
    if not state.get('creating') and not state.get('udid'):
        cleanup['simulatorRemoved'] = True
        return
    inventory = devices(commands, cleaning=True)
    udid = state.get('udid')
    matches = [row for row in inventory if row['name'] == state['name'] or row['udid'] == udid]
    require(len(matches) <= 1 and all(row['name'] == state['name'] and (not udid or row['udid'] == udid) for row in matches),
            'Ambiguous simulator ownership; retain fence')
    if matches:
        device = matches[0]
        udid = state['udid'] = device['udid']
        save(commands.run / 'simulator.json', state)
        if device['state'] == 'Booted':
            native = [row for row in owned_workers(commands, state, cleaning=True) if row['executable'] == EXECUTABLE]
            if native:
                require(all(row['uid'] == os.getuid() and 'owned-device' in row['basis'] for row in native),
                        'Unknown native-process ownership; retain fence')
                cleanup['forcedNativeTermination'] = True
                save(commands.reports / 'cleanup.json', cleanup)
                commands.call(['/usr/bin/xcrun', 'simctl', 'terminate', udid, PACKAGE], 'cleanup-owned-native', seconds=5, cleaning=True)
            if state.get('installIntended'):
                commands.call(['/usr/bin/xcrun', 'simctl', 'uninstall', udid, PACKAGE], 'cleanup-probe-package', seconds=5, cleaning=True)
                require(PACKAGE not in app_inventory(commands, udid, 'cleanup', cleaning=True), 'Probe registration survived cleanup')
        if device['state'] != 'Shutdown':
            commands.call(['/usr/bin/xcrun', 'simctl', 'shutdown', udid], 'shutdown-owned-simulator', seconds=15, cleaning=True)
        current = [row for row in devices(commands, cleaning=True) if row['udid'] == udid]
        require(len(current) == 1 and current[0]['state'] == 'Shutdown', 'Owned simulator shutdown unproven')
        commands.call(['/usr/bin/xcrun', 'simctl', 'delete', udid], 'delete-owned-simulator', seconds=15, cleaning=True)
    require(not any(row['name'] == state['name'] or row['udid'] == udid for row in devices(commands, cleaning=True)), 'Owned simulator remains')
    cleanup['simulatorRemoved'] = True


def main():
    os.umask(0o077)
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, interrupted)
    source = Path(__file__).resolve()
    request = read_json(source.with_name('app8-apple.request.json'))
    require(not sys.argv[1:] and request['authorization'] == 'APP8_APPLE_SINGLE_RUN_AUTHORIZED', 'Private Apple draft has no execution authorization')
    require(request['schema'] == 'app8-apple-private-v1' and request['issueSha'] == ISSUE and request['historicalSha'] == BASE
            and request['probeManifestSha256'] == MANIFEST and request['policySha256'] == POLICIES
            and request['orchestratorSha256'] == digest(source) and request['shippingBuildInputSha256'] == BUILD_HASH
            and request['shippingPolicyPath'] == POLICY_PATH and request['acceptedSourceGuardResultSha256'] == GUARD_HASH
            and request['pfDisposableAmendmentSha256'] == '6d8060b5cba21386326c8cafc96827ef0cb4abad9f3981cb96cdfc15f478109e'
            and request['exclusiveDisposableJobVm'] is True, 'Unbound Apple request/source/disposable-VM authority')
    require(request['toolchain'] == {'developerDir': XCODE, 'sdkVersion': '26.4', 'runtimeIdentifier': RUNTIME,
            'runtimeVersion': '26.4.1', 'runtimeBuild': '23E254a', 'deviceType': DEVICE_TYPE, 'deploymentTarget': '15.0',
            'pfctlSha256': PF_HASH, 'macOS': '26.6.2', 'kernel': '25.6.0'}, 'Unsupported installed toolchain/PF profile')
    require(os.environ.get('GITHUB_REPOSITORY') == 'kira-manga/kira-admin'
            and os.environ.get('GITHUB_REF') == 'refs/heads/remediation/app-29-backend-complaints'
            and os.environ.get('GITHUB_EVENT_NAME') == 'push' and os.environ.get('GITHUB_RUN_ATTEMPT') == '1'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted' and os.environ.get('ImageOS') == 'macos26'
            and platform.system() == 'Darwin' and platform.machine() == 'arm64' and os.getuid() > 0, 'Wrong exclusive standard hosted macOS invocation')
    run = Path(os.environ['APP8_RUN'])
    require(run.is_absolute() and run.parent.resolve() == Path(os.environ['RUNNER_TEMP']).resolve()
            and re.fullmatch(r'app8-apple-[1-9][0-9]*-1', run.name)
            and run.name == 'app8-apple-' + os.environ['GITHUB_RUN_ID'] + '-1' and not run.exists(), 'Not a fresh owned Apple run path')
    run.mkdir(mode=0o700)
    for name in ('reports', 'work', 'inputs'):
        (run / name).mkdir(mode=0o700)
    reports, deadline = run / 'reports', time.monotonic() + 540
    save(run / 'owner.json', {'run': run.name, 'carrierSha': os.environ['GITHUB_SHA'], 'issueSha': ISSUE})
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(Path.home().resolve()),
           'LANG': 'C', 'LC_ALL': 'C', 'DEVELOPER_DIR': XCODE}
    commands = Commands(run, env, deadline - 70)
    state = {'name': 'App8-' + run.name.removeprefix('app8-apple-'), 'creating': False, 'udid': None}
    firewall = {'loadAttempted': False, 'enableAttempted': False, 'tokenPersisted': False,
                'ownershipVerified': False, 'ownershipLost': False, 'releaseAttempted': False, 'disabledAfterRelease': False}
    cleanup = {'nativeAbsent': False, 'fixturesAbsent': False, 'commandsAbsent': False, 'workersAbsent': False,
               'simulatorRemoved': False, 'receiptSaved': False, 'forcedNativeTermination': False, 'errors': []}
    token, fixtures, passed, failure = None, [], False, None
    try:
        deadline_capabilities()
        require(Path(XCODE).is_dir(), 'Missing installed Xcode; no download/install')
        inputs = bind_inputs(source, run, request)  # All frozen source bindings precede any tool/PF/native command.
        require(digest(Path('/sbin/pfctl')) == PF_HASH and platform.release() == '25.6.0', 'Unreviewed installed PF/kernel profile')
        require(commands.call(['/usr/bin/sw_vers', '-productVersion'], 'macos').strip() == '26.6.2', 'Unsupported macOS PF profile')
        check_pf(pf_snapshot(commands, 'pf-baseline'))  # Before compiler/package or simulator mutation too.
        bundles = prepare_bundles(commands, inputs)
        create_simulator(commands, state)
        require(not any(row['executable'] == EXECUTABLE for row in owned_workers(commands, state)), 'Unknown pre-existing native probe')
        rule_file = run / 'work/app8-deny.pf'
        rule_file.write_text(PF_RULES)
        check_pf(pf_snapshot(commands, 'pf-before-load'))
        require(digest(Path('/sbin/pfctl')) == PF_HASH and rule_file.read_text() == PF_RULES, 'PF tool/policy bytes changed')
        commands.call(PF + ['-n', '-f', rule_file], 'pf-parse-only', seconds=5)
        require(not pf_enabled(commands.call(PF + ['-s', 'info'], 'pf-disabled-before-load', seconds=5)), 'PF enabled before mutation; STOP')
        sole_reference(commands.call(PF + ['-s', 'References'], 'pf-no-refs-before-load', seconds=5))
        firewall['loadAttempted'] = True
        save(reports / 'pf-lifecycle.json', firewall)
        commands.call(PF + ['-f', rule_file], 'pf-load-fixed-deny', seconds=5)
        firewall['enableAttempted'] = True
        save(reports / 'pf-lifecycle.json', firewall)
        enabled = commands.call(PF + ['-E'], 'pf-acquire-owned-reference', seconds=5)
        token = owned_token(enabled)  # Never derive/guess a release token from a reference inventory.
        save(run / 'pf-owned-token.json', {'token': token, 'policySha256': hashlib.sha256(PF_RULES.encode()).hexdigest()})
        firewall['tokenPersisted'] = True
        save(reports / 'pf-lifecycle.json', firewall)
        require(len(pf_lines(enabled)) == 2 and pf_lines(enabled)[0] == 'pf enabled', 'Unexpected PF acquisition/owner state; retain fence')
        commands.call(PF + ['-F', 'states'], 'pf-flush-prior-states', seconds=5)
        verify_owned_pf(commands, 'pf-armed', token, firewall)
        save(reports / 'pf-lifecycle.json', firewall)
        nonce = secrets.token_hex(16)
        for phase in ('allow', 'deny'):
            verify_owned_pf(commands, 'pf-before-' + phase, token, firewall)
            native_phase(commands, inputs, state, bundles, phase, nonce, fixtures)
            verify_owned_pf(commands, 'pf-after-' + phase, token, firewall)
        passed = True
    except Exception as error:
        failure = str(error)
    finally:
        commands.end = min(time.monotonic() + 60, deadline - 10)
        try:
            cleanup['commandsAbsent'] = commands.drain()
        except Exception as error:
            cleanup['errors'].append('command drain: ' + str(error))
        # Keep the two fixed atomic fixture receipts, including failure, before scratch/device deletion.
        for phase in ('allow', 'deny'):
            try:
                directory = run / ('work/' + phase + '/fixture')
                records = {name: read_json(directory / (name + '.json')) for name in ('ready', 'receipt')
                           if (directory / (name + '.json')).exists()}
                if records:
                    save(reports / (phase + '-fixture.json'), records)
            except Exception as error:
                cleanup['errors'].append('fixture receipt retention: ' + str(error))
        try:
            if state.get('resultPath') and Path(state['resultPath']).exists():
                save(reports / (state['phase'] + '-native-data.json'), read_json(Path(state['resultPath'])))
        except Exception as error:
            cleanup['errors'].append('native data receipt retention: ' + str(error))
        try:
            dispose_simulator(commands, state, cleanup)
        except Exception as error:
            cleanup['errors'].append('owned simulator/native cleanup: ' + str(error))
        try:
            absence_barrier(commands, state, cleanup, fixtures)
        except Exception as error:
            cleanup['receiptSaved'] = False
            cleanup['errors'].append('absence proof: ' + str(error))
        # The deny configuration stays installed. No old/permissive reload or global disable exists.
        try:
            token_record = read_json(run / 'pf-owned-token.json') if firewall['tokenPersisted'] else None
            if may_release(token_record, token, firewall['ownershipVerified'], cleanup) and not commands.audit_failed:
                cleanup['commandsAbsent'], cleanup['receiptSaved'] = False, False
                verify_owned_pf(commands, 'pf-before-owned-release', token, firewall, cleaning=True)
                absence_barrier(commands, state, cleanup, fixtures)
                require(may_release(token_record, token, firewall['ownershipVerified'], cleanup) and not commands.audit_failed,
                        'Final fresh absence/receipt failed; retain fence')
                require(read_json(run / 'pf-owned-token.json') == token_record, 'Persisted PF token changed; retain fence')
                firewall['releaseAttempted'] = True
                save(reports / 'pf-lifecycle.json', firewall)
                commands.call(PF + ['-X', token_record['token']], 'pf-release-only-owned-token', seconds=5, cleaning=True)
                final_status = commands.call(PF + ['-s', 'info'], 'pf-final-status', seconds=5, cleaning=True)
                final_refs = commands.call(PF + ['-s', 'References'], 'pf-final-references', seconds=5, cleaning=True)
                require(not pf_enabled(final_status), 'PF still enabled after owned release; no global disable/fallback')
                sole_reference(final_refs)
                firewall['disabledAfterRelease'] = True
            elif firewall['enableAttempted']:
                cleanup['errors'].append('No proven sole-token/full-absence barrier; no release attempted; retain fence/unknown state')
        except Exception as error:
            cleanup['errors'].append('owned PF release/readback: ' + str(error))
        scratch_removed = False
        try:
            absence_barrier(commands, state, cleanup, fixtures)
            require(all(cleanup[key] for key in ('nativeAbsent', 'fixturesAbsent', 'commandsAbsent', 'workersAbsent', 'simulatorRemoved')),
                    'Incomplete owned absence; retain scratch')
            require(not firewall['enableAttempted'] or firewall['disabledAfterRelease'], 'PF token/final state unresolved; retain scratch')
            for name in ('work', 'inputs'):
                shutil.rmtree(run / name)
            for name in ('owner.json', 'simulator.json', 'pf-owned-token.json'):
                (run / name).unlink(missing_ok=True)
            scratch_removed = True
        except Exception as error:
            cleanup['errors'].append('scratch: ' + str(error))
        output_ok = commands.within_cap()  # No truncation can manufacture a passing result.
        normal = commands.normal() and not cleanup['forcedNativeTermination'] and not cleanup['errors']
        passed = passed and normal and output_ok and scratch_removed and firewall['disabledAfterRelease'] and not CANCELLED
        if not output_ok:
            remaining = 4194304
            for path in sorted(reports.glob('*.log')):
                keep = min(1048576, remaining, path.stat().st_size)
                with path.open('r+b') as output:
                    output.truncate(keep)
                remaining -= keep
        save(reports / 'cleanup.json', cleanup)
        save(reports / 'pf-lifecycle.json', firewall)
        save(reports / 'result.json', {'passed': passed, 'failure': failure, 'normalOwnedCleanup': normal,
             'ownedScratchRemoved': scratch_removed, 'logsWithinCapBeforeTruncation': output_ok,
             'pfDisabledAfterOwnedRelease': firewall['disabledAfterRelease'], 'priorPFConfigurationRestored': False,
             'inactiveDenyConfigurationDisposition': 'discard with exclusive job VM; not restoration',
             'carrierSha': os.environ['GITHUB_SHA'], 'run': run.name,
             'issueSha': ISSUE, 'historicalSha': BASE, 'scope': 'Apple SDK-only copied-policy ARM simulator host',
             'shippingIpaMergeAccepted': False, 'minimumIOS15RuntimeProven': False, 'androidAttempted': False})
    return 0 if passed and not CANCELLED else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('APP8 APPLE INCOMPLETE: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
