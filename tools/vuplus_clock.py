"""Morocco-local source clock and duplicate-collection guard."""
import argparse
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

REPOSITORY = "wacayoub/EPGManager"
BRANCH = b"refs/heads/vuplus-clock"


def schedules(policy):
    return dict(policy["sources"], postprocess=policy["postprocess"],
                watchdog=policy["watchdog"])


def target(schedule, day):
    hour, minute = map(int, schedule["time"].split(":"))
    return datetime.combine(day, time(hour, minute), ZoneInfo(schedule["timezone"]))


def due_tasks(policy, state, now):
    day = now.astimezone(ZoneInfo("Africa/Casablanca")).date()
    sent = state.get("sent", {}) if state.get("day") == day.isoformat() else {}
    return sorted(
        ({"task": task, "day": day.isoformat(), "due": target(row, day).isoformat()}
         for task, row in schedules(policy).items()
         if task not in sent and target(row, day) <= now),
        key=lambda request: request["due"])


def validate_request(policy, request, now):
    task = request["task"]
    day = now.astimezone(ZoneInfo("Africa/Casablanca")).date()
    if request["day"] != day.isoformat():
        raise ValueError("Clock request is outside the current Morocco day")
    if task == "probe":
        return "probe"
    schedule = schedules(policy)[task]
    due = target(schedule, day)
    if request["due"] != due.isoformat() or now < due:
        raise ValueError("Clock request does not match the configured target")
    return "source" if task in policy["sources"] else task


def collection_gate(root, policy, source, event, requested_day, now):
    if source not in policy["sources"]:
        raise ValueError("Unknown source")
    if event != "schedule" and not requested_day:
        return True, "MANUAL_COLLECTION"
    local = now.astimezone(ZoneInfo("Africa/Casablanca"))
    if requested_day:
        if requested_day != local.date().isoformat():
            raise ValueError("Expired external clock request")
        slot_day = local.date()
        if now < target(policy["sources"][source], slot_day):
            raise ValueError("External collection requested before its target")
    else:
        due = target(policy["sources"][source], local.date())
        slot_day = (due if due <= local else due-timedelta(days=1)).date()
    path = Path(root)/"reports"/(source+"-publish-lkg.json")
    if path.is_file():
        report = json.loads(path.read_text(encoding="utf-8"))
        row = report.get("sources", {}).get(source, {})
        stamp = datetime.fromisoformat(report["generated_utc"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Publication attempt has no timezone")
        if (row.get("decision") in ("PUBLISH", "KEEP_LKG")
                and report.get("github_run_id")
                and stamp.astimezone(local.tzinfo).date() >= slot_day):
            return False, "ALREADY_ATTEMPTED_ON_LOCAL_DAY"
    return True, "DAILY_COLLECTION_DUE"


def atomic_json(path, data):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(data, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def push_request(home, request):
    runtime = json.loads((home/"runtime.json").read_text())
    wheel = home/runtime["filename"]
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != runtime["sha256"]:
        raise ValueError("Git transport checksum mismatch")
    sys.path.insert(0, str(wheel))
    from dulwich.client import SSHGitClient, SSHVendor, SubprocessWrapper
    from dulwich.objects import Blob, Commit, Tree
    from dulwich.repo import MemoryRepo

    processes = []
    class ReceiverSSH(SSHVendor):
        def run_command(self, host, command, username=None, port=None, **kwargs):
            if host != "ssh.github.com" or username != "git" or port != 443:
                raise ValueError("Unexpected Git destination")
            process = subprocess.Popen(
                ["/usr/bin/dbclient", "-T", "-I", "35", "-p", "443",
                 "-i", str(home/"deploy-key"), "git@ssh.github.com",
                 command.decode("utf-8")],
                bufsize=0, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes.append(process)
            return SubprocessWrapper(process)

    repo = MemoryRepo()
    blob = Blob.from_string((home/"vuplus-clock.yml").read_bytes())
    workflow = Tree()
    workflow.add(b"vuplus-clock.yml", 0o100644, blob.id)
    github = Tree()
    github.add(b"workflows", 0o40000, workflow.id)
    tree = Tree()
    tree.add(b".github", 0o40000, github.id)
    commit = Commit()
    commit.tree = tree.id
    commit.parents = []
    commit.author = commit.committer = b"EPGManager source clock <clock@epgmanager.local>"
    commit.author_time = commit.commit_time = int(datetime.fromisoformat(request["due"]).timestamp())
    commit.author_timezone = commit.commit_timezone = 0
    commit.message = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    for item in (blob, workflow, github, tree, commit):
        repo.object_store.add_object(item)
    client = SSHGitClient("ssh.github.com", port=443, username="git", vendor=ReceiverSSH())
    try:
        result = client.send_pack(REPOSITORY+".git",
                                  lambda refs: {BRANCH: commit.id},
                                  repo.generate_pack_data)
        if result.ref_status.get(BRANCH):
            raise RuntimeError("Clock branch push rejected")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
    return commit.id.decode()


def tick(home, policy, probe, now):
    import fcntl
    with (home/"clock.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        state_path = home/"state.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        day = now.astimezone(ZoneInfo("Africa/Casablanca")).date().isoformat()
        if state.get("day") != day:
            state = {"day": day, "sent": {}}
        state["last_tick_utc"] = now.isoformat()
        requests = ([{"task": "probe", "day": day, "due": now.isoformat()}]
                    if probe else due_tasks(policy, state, now))
        if requests:
            request = requests[0]
            try:
                def expired(signum, frame):
                    raise TimeoutError("Clock delivery exceeded 45 seconds")
                signal.signal(signal.SIGALRM, expired)
                signal.alarm(45)
                commit = push_request(home, request)
                state["last_delivery"] = dict(request, commit=commit, sent_utc=now.isoformat())
                if not probe:
                    state["sent"][request["task"]] = state["last_delivery"]
                state["last_error"] = ""
            except Exception as error:
                state["last_error"] = type(error).__name__+": "+str(error)[:180]
            finally:
                signal.alarm(0)
        atomic_json(state_path, state)
        print(json.dumps(state.get("last_delivery") or {"last_error": state.get("last_error", "")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("tick", "validate-request", "gate"))
    parser.add_argument("--home", type=Path, default=Path("/etc/epgmanager-clock"))
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--source")
    parser.add_argument("--requested-day", default="")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    args = parser.parse_args()
    path = args.policy or args.home/"source-schedules.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    if args.mode == "tick":
        tick(args.home, policy, args.probe, now)
    elif args.mode == "validate-request":
        request = json.loads(os.environ["CLOCK_REQUEST"])
        kind = validate_request(policy, request, now)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write("kind="+kind+"\ntask="+request["task"]+"\nday="+request["day"]+"\n")
        print("Clock request validated", json.dumps(request))
    else:
        run, reason = collection_gate(".", policy, args.source, args.event,
                                      args.requested_day, now)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write("run="+str(run).lower()+"\n")
        print(reason, args.source)


if __name__ == "__main__":
    main()
