#!/usr/bin/env python3
"""Original orphaned App HTTPS server lifecycle only; no simulator or product qualification."""
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import ssl
import subprocess
import sys
import time

SCRIPT = Path(__file__).resolve()
WORKFLOW = ".github/workflows/single-hop-lifecycle-diagnostic.yml"
APP_SHA = "88ec235bcc2530fe458a25221dcf16eee1e4535d"
HELPER_SHA = "beaf23922b058941694fd2b46c3c2730264d9d80516b4853e73ce7d213885a90"
REF = "refs/heads/remediation/priority-sources-20261017"
PS = ["/bin/ps", "-ww", "-axo", "pid=,stat=,command="]
CONTEXT = ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "GITHUB_REPOSITORY", "GITHUB_REPOSITORY_ID",
           "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA", "GITHUB_REF", "GITHUB_WORKFLOW_REF",
           "GITHUB_WORKFLOW_SHA", "GITHUB_WORKSPACE", "RUNNER_TEMP")


def read(path):
    assert path.is_file() and not path.is_symlink() and path.stat().st_size <= 65536
    return json.loads(path.read_text())


def save(path, value):
    data = json.dumps(value, sort_keys=True) + "\n"
    assert len(data) <= 65536
    path.write_text(data)


def runtime():
    env = {key: os.environ[key] for key in CONTEXT}
    assert env["GITHUB_ACTIONS"] == "true" and env["GITHUB_EVENT_NAME"] == "push"
    assert env["GITHUB_REPOSITORY"] == "kira-manga/kira-source-engine" and env["GITHUB_REPOSITORY_ID"] == "1310736214"
    assert env["GITHUB_REF"] == REF and env["GITHUB_RUN_ATTEMPT"] == "1"
    assert env["GITHUB_WORKFLOW_REF"] == "kira-manga/kira-source-engine/" + WORKFLOW + "@" + REF
    assert env["GITHUB_SHA"] == env["GITHUB_WORKFLOW_SHA"] and re.fullmatch(r"[0-9a-f]{40}", env["GITHUB_SHA"])
    assert re.fullmatch(r"[1-9][0-9]*", env["GITHUB_RUN_ID"])
    assert platform.system() == "Darwin" and platform.machine() == "arm64"
    workspace, temporary = (Path(env[key]).resolve(strict=True) for key in ("GITHUB_WORKSPACE", "RUNNER_TEMP"))
    assert SCRIPT.parents[2] == workspace / "control"
    root = temporary / ("kira-single-hop-diagnostic-" + env["GITHUB_RUN_ID"] + "-1")
    fixture = temporary / ("kira-single-hop-" + env["GITHUB_RUN_ID"] + "-1-ios")
    helper = workspace / "app/scripts/ci/single-hop-loopback.py"
    assert helper.is_file() and not helper.is_symlink() and helper.stat().st_size <= 32768
    assert hashlib.sha256(helper.read_bytes()).hexdigest() == HELPER_SHA
    git_env = {"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    for checkout, expected in ((workspace / "control", env["GITHUB_SHA"]), (workspace / "app", APP_SHA)):
        actual = subprocess.check_output(["/usr/bin/git", "--no-optional-locks", "-C", str(checkout),
                                          "rev-parse", "HEAD"], env=git_env, timeout=10, text=True).strip()
        assert actual == expected
    output = os.environ.get("GITHUB_OUTPUT")
    os.environ.clear()
    os.environ.update(env, PATH=os.defpath, HOME=str(root), TMPDIR=str(root))
    owner = {key: env[key] for key in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")}
    owner.update(app_sha=APP_SHA, helper_sha256=HELPER_SHA, diagnostic_sha256=hashlib.sha256(SCRIPT.read_bytes()).hexdigest())
    return root, fixture, helper, owner, output


def error_code(error, helper):
    # These line identities are admitted only with the exact HELPER_SHA above.
    reasons = {25: "metadata_file_bound", 230: "ps_output_bound", 236: "pid_match_cardinality",
               239: "recorded_pid_shape", 240: "recorded_pid_argv_identity", 247: "signal_delivery",
               255: "stop_exhausted", 277: "owner_mismatch", 290: "cleanup_action_failure"}
    trace, reason = error.__traceback__, "unclassified_failure"
    while trace:
        if trace.tb_frame.f_code.co_filename == str(helper):
            reason = reasons.get(trace.tb_lineno, reason)
        trace = trace.tb_next
    return reason


def phase(root, fixture, helper, action, label):
    assert action in ("prepare", "cleanup") and re.fullmatch(r"(?:attempt-[1-6]|final|backstop)-(?:prepare|cleanup)", label)
    assert not (fixture / "simulator.json").exists()  # No simctl/keychain invocation in this diagnostic.
    spec = importlib.util.spec_from_file_location("original_fixture", helper)
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    original.simulator = lambda _root: None  # Explicit server-only scope; no simulator is allocated.
    original_run, original_pids = original.run, original.server_pids
    events, position = [], "outside_observer"
    recorded = read(fixture / "server-ready.json")["server_pid"] if (fixture / "server-ready.json").exists() else None

    def event(value):
        if len(events) == 64:
            events.pop(1)  # Preserve the first observation and the bounded most recent tail.
        events.append(dict(value, phase=position))

    def observed_run(args, owned_root, timeout=30):
        try:
            output = original_run(args, owned_root, timeout)
        except Exception:
            event({"reason": "ps_tool_failure" if args == PS else "fixture_tool_failure"})
            raise
        if args == PS and len(output) <= 2 * 1024 * 1024 and type(recorded) is int:
            row = next((line.split(None, 2) for line in output.splitlines()
                        if line.split(None, 1)[:1] == [str(recorded)]), [])
            state = row[1] if len(row) >= 2 and re.fullmatch(r"[A-Za-z0-9+<>?_-]{1,16}", row[1]) else "unavailable"
            suffix = " -I -B " + str(helper) + " serve " + str(fixture)
            matches = len(row) == 3 and row[2].endswith(suffix) and bool(
                re.fullmatch(r"/\S*/(?:python3(?:\.[0-9]+)?|Python)", row[2][:-len(suffix)]))
            event({"recorded_pid": recorded, "exists": bool(row), "state": state, "args_match": matches})
        return output  # Original output and all original assertions are unchanged.

    def observed_pids(owned_root):
        nonlocal position
        caller = sys._getframe(1)
        position = {245: "before_signal", 252: "await_exit"}.get(caller.f_lineno, "other_call")
        try:
            return original_pids(owned_root)
        except Exception as error:
            event({"reason": error_code(error, helper)})
            raise

    original.run, original.server_pids = observed_run, observed_pids
    result = {"action": action, "events": events, "call_exit_code": 0}
    try:
        getattr(original, action)("ios")
    except (Exception, KeyboardInterrupt) as error:
        result.update(call_exit_code=1, reason=error_code(error, helper))
    finally:
        for name in ("cleanup", "stats"):
            if (fixture / (name + ".json")).exists():
                result[name] = read(fixture / (name + ".json"))
        save(root / "evidence" / (label + ".json"), result)
    return result["call_exit_code"]


def invoke(root, action, label, deadline=None):
    seconds = 45 if action == "prepare" else 55
    if deadline is not None:
        seconds = min(seconds, max(0, deadline - time.monotonic()))
    if seconds <= 0:
        save(root / "evidence" / (label + "-deadline.json"), {"reason": "phase_deadline"})
        return 124
    process = subprocess.Popen([sys.executable, "-I", "-B", str(SCRIPT), "phase-" + action, label],
                               env=dict(os.environ), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        return process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        save(root / "evidence" / (label + "-deadline.json"), {"reason": "phase_deadline"})
        return 124
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)


def clean(root, fixture, label, deadline=None):
    if not fixture.exists() and not fixture.is_symlink():
        return True
    code = invoke(root, "cleanup", label, deadline)
    if code != 0:
        return False
    assert fixture == fixture.resolve(strict=True) and not fixture.is_symlink()
    assert read(fixture / "cleanup.json") == {"server_stopped": True, "simulator_deleted": True,
                                               "secret_files_removed": True, "failures": []}
    assert not (fixture / "ready.json").exists() or (fixture / "stats.json").is_file()
    assert shutil.rmtree.avoids_symlink_attacks
    shutil.rmtree(fixture)
    return not fixture.exists()


def ready_request(fixture):
    ready = read(fixture / "ready.json")
    match = re.fullmatch(r"https://localhost:([1-9][0-9]{0,4})", ready["origin"])
    assert match and re.fullmatch(r"[0-9a-f]{32}", ready["fixture_id"])
    connection = http.client.HTTPSConnection("localhost", int(match[1]), timeout=5,
        context=ssl.create_default_context(cafile=str(fixture / "ca.pem")))
    try:
        connection.request("GET", "/ready", headers={"X-Kira-Fixture": ready["fixture_id"]})
        response = connection.getresponse()
        assert response.status == 200 and response.read(256) == ready["fixture_id"].encode()
    finally:
        connection.close()


def diagnostic(root, fixture, owner, output):
    assert not fixture.exists() and not fixture.is_symlink()
    root.mkdir(mode=0o700)
    (root / "evidence").mkdir(mode=0o700)
    save(root / "owner.json", owner)
    save(root / "evidence/owner.json", owner)
    if output:
        with open(output, "a") as stream:
            stream.write("root=" + str(root) + "\n")
    result = {"scope": "server-only-diagnostic-not-product", "no_reproduction_is_not_a_fix": True, "attempts": []}
    deadline = time.monotonic() + 100  # Reserve the original cleanup retry within the three-minute step.
    try:
        for number in range(1, 7):
            if time.monotonic() >= deadline:
                result["stopped_at_time_bound"] = True
                break
            attempt = {"number": number}
            result["attempts"].append(attempt)
            attempt["prepare_exit_code"] = invoke(root, "prepare", f"attempt-{number}-prepare", deadline)
            if attempt["prepare_exit_code"] != 0:
                break
            ready_request(fixture)
            attempt["cleanup_ok"] = clean(root, fixture, f"attempt-{number}-cleanup", deadline)
            if not attempt["cleanup_ok"]:
                break  # Preserve the first refusal; never loop until a desired PASS appears.
    except (Exception, KeyboardInterrupt):
        result["controller_failure"] = True
    finally:
        result["final_cleanup_ok"] = False
        try:
            result["final_cleanup_ok"] = clean(root, fixture, "final-cleanup")
        finally:
            save(root / "evidence/result.json", result)
    return 0 if result["final_cleanup_ok"] and result["attempts"] and all(
        item.get("cleanup_ok") is True for item in result["attempts"]) and not result.get("controller_failure") else 1


def main():
    os.umask(0o077)
    root, fixture, helper, owner, output = runtime()
    mode = sys.argv[1]
    if mode == "run":
        assert len(sys.argv) == 2
        return diagnostic(root, fixture, owner, output)
    assert root == root.resolve(strict=True) and not root.is_symlink() and read(root / "owner.json") == owner
    if mode.startswith("phase-"):
        assert len(sys.argv) == 3
        return phase(root, fixture, helper, mode.removeprefix("phase-"), sys.argv[2])
    assert len(sys.argv) == 2
    if mode == "cleanup":
        ok = clean(root, fixture, "backstop-cleanup")
        save(root / "evidence/finalizer.json", {"owned_fixture_absent": ok})
        return 0 if ok else 1
    assert mode == "dispose" and read(root / "evidence/finalizer.json") == {"owned_fixture_absent": True}
    assert not fixture.exists() and not fixture.is_symlink() and shutil.rmtree.avoids_symlink_attacks
    shutil.rmtree(root)
    return 0


def interrupted(*_):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt):
        print("Owned server-only diagnostic refused; inspect bounded static evidence.", file=sys.stderr)
        sys.exit(1)
