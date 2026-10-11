"""Run the unchanged recorder in an independently constrained Linux container.

This orchestration is outside the solver and leaves original observations
untouched. The recorder itself is not a correctness validator or sandbox.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys


def file_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="ascii").strip()
    except OSError:
        return None


def environment() -> dict:
    affinity = sorted(os.sched_getaffinity(0))
    cpu_max = file_text("/sys/fs/cgroup/cpu.max")
    quota = None
    if cpu_max:
        amount, period = cpu_max.split()
        if amount != "max":
            quota = int(amount) / int(period)
    capacity = min(len(affinity), quota) if quota is not None else len(affinity)
    memory_max = file_text("/sys/fs/cgroup/memory.max")
    interfaces = [name for _, name in socket.if_nameindex()]
    result = {"platform": platform.platform(), "python": sys.version,
              "logical_cpus": os.cpu_count(), "affinity_cpus": affinity,
              "cgroup_cpu_max": cpu_max, "effective_vcpus": capacity,
              "cgroup_memory_max": memory_max, "network_interfaces": interfaces,
              "container_image": os.environ.get("DIRECTOR_IMAGE", "not supplied"),
              "git_commit": os.environ.get("DIRECTOR_COMMIT", "not supplied"),
              "restriction_note": "256 MiB cgroup cap is shared by recorder, meter and solver; swap disabled by launcher."}
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Measurements require Python 3.11")
    if capacity != 2:
        raise RuntimeError("Measured effective CPU capacity must equal two vCPUs")
    if memory_max is None or memory_max == "max" or int(memory_max) > 256 * 1024 * 1024:
        raise RuntimeError("A verified cgroup memory cap of at most 256 MiB is required")
    if any(name != "lo" for name in interfaces):
        raise RuntimeError("Measurement container must have no non-loopback network interface")
    return result


def run_logged(command: list[str], path: Path, cwd: Path) -> int:
    with path.open("wb") as log:
        completed = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                   check=False, timeout=600)
    print(path.read_text(encoding="utf-8", errors="replace"), end="", flush=True)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Run this script in the Linux measurement container")
    evidence = environment()
    results = args.results.resolve()
    results.mkdir(parents=True, exist_ok=True)
    package = results / "package"
    shutil.copytree(args.source.resolve(), package, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".pytest_cache", "*.pyc", "*.zip",
        "benchmark_results.json", "benchmark_run.log", "local_smoke_results.json",
        "regression_run.log", "validation_report.json", "measurement_environment.json"))
    (package / "measurement_environment.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    regression_status = run_logged([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                                   package / "regression_run.log", package)
    recorder_status = run_logged([sys.executable, str(package / "benchmark_runner.py"),
                                  "--solution", str(package / "context_solver.py"),
                                  "--inputs", str(package / "benchmark_inputs.json"),
                                  "--output", str(package / "benchmark_results.json")],
                                 package / "benchmark_run.log", results)
    if recorder_status:
        print("Recorder infrastructure failed; original execution log retained.", file=sys.stderr)
        return recorder_status
    check_status = run_logged([sys.executable, str(package / "independent_check.py"),
                               "--inputs", str(package / "benchmark_inputs.json"),
                               "--observations", str(package / "benchmark_results.json"),
                               "--output", str(package / "validation_report.json")],
                              package / "validation_run.log", results)
    observations = json.loads((package / "benchmark_results.json").read_text(encoding="utf-8"))
    readme = package / "README.md"
    stage_note = ("Current source handoff has not yet run the remote workflow. Consequently it\n"
                  "contains no fabricated `benchmark_results.json` or `benchmark_run.log`.\n"
                  "In a final workflow-produced ZIP, those files are actual measurements from that\n"
                  "run; the reported environment in those files supersedes this source-stage note.")
    measured_note = (f"This archive contains {len(observations['instances'])} actual observations from the unchanged\n"
                     "recorder and its captured execution log. See `measurement_environment.json`\n"
                     "and the recorder's environment metadata for the measured platform, Python,\n"
                     "image digest and CPU restrictions. `regression_run.log` and\n"
                     "`validation_report.json` record actual checks; unsuccessful requests are\n"
                     "retained. Official large-instance 99% quality remains unverified without\n"
                     "the assessment's frozen comparison utilities.")
    readme.write_text(readme.read_text(encoding="utf-8").replace(stage_note, measured_note), encoding="utf-8")
    subprocess.run([sys.executable, str(package / "build_package.py"),
                    "--root", str(package), "--output", str(results / "director-context.zip"),
                    "--require-results"], check=True)
    return regression_status or check_status


if __name__ == "__main__":
    raise SystemExit(main())
