"""Observations-only recorder. Contains no grading answers or capability tags.

Run on Linux against the delivered Python entrypoint. The candidate must retain
this supplied file unchanged; its public interface is only a subprocess recorder.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import pathlib
import platform
import signal
import subprocess
import sys
import time


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def source_inventory(root):
    """Record delivered Python modules without importing or executing them."""
    root = root.resolve()
    inventory = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError("Python source resolves outside the delivered package")
        inventory[path.relative_to(root).as_posix()] = sha256(path.read_bytes())
    return inventory



def cpu_capacity():
    """Disclose logical CPUs, actual affinity and Linux cgroup quota separately."""
    data = {"logical_cpus": os.cpu_count(), "affinity_cpus": None, "cgroup_cpu_max": None,
            "quota_vcpus": None}
    if hasattr(os, "sched_getaffinity"):
        data["affinity_cpus"] = sorted(os.sched_getaffinity(0))
    path = pathlib.Path("/sys/fs/cgroup/cpu.max")
    if sys.platform.startswith("linux") and path.is_file():
        try:
            quota, period = path.read_text(encoding="ascii").strip().split()
            data["cgroup_cpu_max"] = quota + " " + period
            if quota != "max" and int(period) > 0:
                data["quota_vcpus"] = int(quota) / int(period)
        except (OSError, ValueError):
            pass
    return data


def child_meter(solution, timeout):
    """A separate trusted meter process makes RUSAGE_CHILDREN per invocation."""
    import resource
    payload = sys.stdin.buffer.read()
    began = time.perf_counter()
    proc = subprocess.Popen([sys.executable, solution], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    expired = False
    try:
        stdout, stderr = proc.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        expired = True
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
    elapsed_ms = (time.perf_counter() - began) * 1000
    rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if sys.platform == "darwin":
        rss = math.ceil(rss / 1024)
    output = {"stdout": stdout.decode("utf-8", errors="replace"), "stderr": stderr.decode("utf-8", errors="replace"),
              "exit_code": proc.returncode, "wall_ms": elapsed_ms, "peak_rss_kib": int(rss), "timed_out": expired}
    sys.stdout.write(json.dumps(output, ensure_ascii=False, allow_nan=False) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solution", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--meter", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.meter:
        child_meter(str(pathlib.Path(args.solution).resolve()), args.timeout)
        return
    source = pathlib.Path(args.inputs).read_bytes()
    corpus = json.loads(source)
    if set(corpus) != {"schema", "instances"} or corpus["schema"] != "openart-context-inputs/2":
        raise ValueError("Unsupported input corpus")
    ids = [item["id"] for item in corpus["instances"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate instance ID")
    solution = pathlib.Path(args.solution).resolve()
    sources_before = source_inventory(solution.parent)
    records = []
    for item in corpus["instances"]:
        if set(item) != {"id", "request"}:
            raise ValueError("Invalid input envelope")
        payload = canonical(item["request"])
        # Benchmark IDs are intentionally not passed to the candidate process.
        command = [sys.executable, str(pathlib.Path(__file__).resolve()), "--meter", "--solution", str(solution),
                   "--inputs", str(pathlib.Path(args.inputs).resolve()), "--output", args.output, "--timeout", str(args.timeout)]
        observed = subprocess.run(command, input=payload + b"\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=args.timeout + 10, check=False)
        if observed.returncode:
            raise RuntimeError("Meter failed: " + observed.stderr.decode("utf-8", errors="replace"))
        record = json.loads(observed.stdout)
        record.update(id=item["id"], input_sha256=sha256(payload))
        try:
            record["response"] = json.loads(record["stdout"], parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            record["response_parse_error"] = None
        except (ValueError, UnicodeError) as exc:
            record["response"] = None
            record["response_parse_error"] = str(exc)
        records.append(record)
        print(json.dumps({"id": item["id"], "exit_code": record["exit_code"], "wall_ms": round(record["wall_ms"], 3),
                          "peak_rss_kib": record["peak_rss_kib"], "timed_out": record["timed_out"]}), flush=True)
    sources_after = source_inventory(solution.parent)
    result = {"schema": "openart-context-observations/2", "inputs_sha256": sha256(source),
              "python_sources_before": sources_before, "python_sources_after": sources_after,
              "python_sources_unchanged": sources_before == sources_after,
              "runner_sha256": sha256(pathlib.Path(__file__).read_bytes()), "entrypoint_sha256": sha256(solution.read_bytes()),
              "environment": {"python": sys.version, "executable": sys.executable, "platform": platform.platform(),
                              "logical_cpus": os.cpu_count(), "cpu_capacity": cpu_capacity(), "rss_units": "KiB", "wall_limit_seconds": args.timeout,
                              "invocation": " ".join(sys.argv)}, "instances": records}
    pathlib.Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
