"""Build the source handoff or final ZIP without changing observations."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def build(root: Path, output: Path, require_results: bool = False) -> None:
    root = root.resolve()
    output = output.resolve()
    required = ["context_solver.py", "context_model.py", "context_search.py", "context_certificate.py", "README.md",
                "benchmark_runner.py", "benchmark_inputs.json", "tests/test_context.py"]
    if require_results:
        required += ["benchmark_results.json", "benchmark_run.log"]
    for name in required:
        if not (root / name).is_file():
            raise ValueError("Missing required file: " + name)
    if require_results:
        observations = json.loads((root / "benchmark_results.json").read_text(encoding="utf-8"))
        corpus = json.loads((root / "benchmark_inputs.json").read_text(encoding="utf-8"))
        if observations.get("schema") != "openart-context-observations/2":
            raise ValueError("Results are not original recorder observations")
        if [x["id"] for x in observations["instances"]] != [x["id"] for x in corpus["instances"]]:
            raise ValueError("Recorder did not observe every corpus request in order")
        if not observations.get("python_sources_unchanged"):
            raise ValueError("Python sources changed during measurement")
        inventory = observations["python_sources_after"]
        current = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in root.rglob("*.py") if "__pycache__" not in p.parts}
        if inventory != current:
            raise ValueError("Delivered Python sources differ from measured sources")
        if observations["inputs_sha256"] != hashlib.sha256((root / "benchmark_inputs.json").read_bytes()).hexdigest():
            raise ValueError("Delivered corpus differs from measured corpus")
        if observations["runner_sha256"] != hashlib.sha256((root / "benchmark_runner.py").read_bytes()).hexdigest():
            raise ValueError("Delivered recorder differs from measured recorder")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if not path.is_file() or path.resolve() == output:
                continue
            if any(part in {".git", "__pycache__", ".pytest_cache"} for part in relative.parts):
                continue
            if path.suffix in {".pyc", ".zip"}:
                continue
            archive.write(path, relative.as_posix())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-results", action="store_true")
    args = parser.parse_args()
    build(args.root, args.output, args.require_results)
    print(str(args.output.resolve()))


if __name__ == "__main__":
    main()
