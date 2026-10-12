"""Portable corpus smoke run. This is NOT the supplied Linux recorder."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

from independent_check import check_response


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", type=Path, default=Path(__file__).with_name("benchmark_inputs.json"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("local_smoke_results.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    def inventory():
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*.py") if "__pycache__" not in p.parts}
    before = inventory()
    corpus = json.loads(args.inputs.read_text(encoding="utf-8"))
    observations = []
    failed = False
    for instance in corpus["instances"]:
        began = time.perf_counter()
        payload = json.dumps(instance["request"], ensure_ascii=False).encode("utf-8")
        try:
            process = subprocess.run([sys.executable, str(Path(__file__).with_name("context_solver.py"))],
                                     input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     timeout=5, check=False)
            stdout, stderr = process.stdout.decode("utf-8"), process.stderr.decode("utf-8")
            response = json.loads(stdout)
            report = check_response(instance["request"], response)
            passed = process.returncode == 0 and not report["core_errors"]
            if response.get("status") == "ok":
                passed &= bool(report["certificate"] and report["certificate"].get("valid"))
            record = {"id": instance["id"], "exit_code": process.returncode,
                      "stdout": stdout, "stderr": stderr, "response": response,
                      "independent_check": report, "passed": passed}
        except (subprocess.TimeoutExpired, ValueError, UnicodeError) as exc:
            passed = False
            record = {"id": instance["id"], "passed": False, "error": str(exc)}
        record["wall_ms"] = (time.perf_counter() - began) * 1000
        observations.append(record)
        failed |= not passed
        print(json.dumps({"id": instance["id"], "passed": passed,
                          "utility": record.get("response", {}).get("utility"),
                          "wall_ms": round(record["wall_ms"], 3)}), flush=True)
    result = {"schema": "director-context-local-smoke/1", "python": sys.version,
              "platform": platform.platform(), "official_recorder": False,
              "note": "Portable development checks only. No RSS, isolation, or target-environment claims.",
              "python_sources_before": before, "python_sources_after": inventory(),
              "inputs_sha256": hashlib.sha256(args.inputs.read_bytes()).hexdigest(),
              "passed": not failed, "instances": observations}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
