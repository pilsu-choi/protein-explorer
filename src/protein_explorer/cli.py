"""Command line entry point for M0."""

import argparse
import platform
import shutil
import subprocess
import sys

from .predict import PredictionError, run_prediction
from .sequence import SequenceError, read_sequence


def _input_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("sequence", nargs="?", help="raw amino-acid sequence or one FASTA record")
    parser.add_argument("--file", help="read raw sequence or one FASTA record from PATH")


def _doctor() -> int:
    print(f"Python: {platform.python_version()} ({platform.python_implementation()})")
    print(f"Platform: {platform.platform()}")
    for name in ("colabfold_batch", "nvidia-smi"):
        path = shutil.which(name)
        print(f"{name}: {path or 'not found'}")
        if name == "nvidia-smi" and path:
            try:
                result = subprocess.run([path, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                                        capture_output=True, text=True, timeout=5, check=False)
                if result.returncode == 0 and result.stdout.strip():
                    for line in result.stdout.strip().splitlines():
                        print(f"GPU: {line}")
                else:
                    detail = result.stderr.strip() or f"nvidia-smi exited with status {result.returncode}"
                    print(f"GPU query failed: {detail}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"GPU query failed: {exc}")
    print("Diagnostics do not inspect environment variables or download software.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="protein-explorer", description="Validate protein sequences and run local ColabFold predictions.")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate one sequence or FASTA record")
    _input_args(validate)
    doctor = commands.add_parser("doctor", help="inspect local Python, GPU, and ColabFold availability")
    doctor.set_defaults()
    predict = commands.add_parser("predict", help="run an installed colabfold_batch executable")
    _input_args(predict)
    predict.add_argument("--colabfold-batch", default="colabfold_batch", help="executable name or path (default: colabfold_batch)")
    predict.add_argument("--output-dir", help="new, unused directory for this prediction")
    predict.add_argument("--timeout", type=int, default=7200, help="maximum runtime in seconds (default: 7200)")
    predict.add_argument("--num-recycle", type=int, default=3, help="ColabFold recycle count (default: 3)")
    predict.add_argument("--num-models", type=int, choices=range(1, 6), default=5,
                         help="number of ColabFold models to run (1-5; default: 5)")
    predict.add_argument("--data-dir", help="ColabFold model cache directory (default: ColabFold's default)")
    predict.add_argument("--model-type", choices=("alphafold2_ptm",), default="alphafold2_ptm")
    predict.add_argument("--msa-mode", choices=("mmseqs2_uniref_env", "single_sequence"),
                         default="mmseqs2_uniref_env", help="public MSA service (default) or sequence-only prediction")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    try:
        sequence = read_sequence(getattr(args, "sequence", None), getattr(args, "file", None))
        if args.command == "validate":
            print(f"Valid sequence: {len(sequence)} residues (normalized).")
            return 0
        if args.msa_mode == "mmseqs2_uniref_env":
            print("Notice: ColabFold's public MSA mode sends the sequence to its external MSA service.", file=sys.stderr)
        directory, manifest = run_prediction(sequence, args.colabfold_batch, args.output_dir, args.timeout,
                                             args.num_recycle, args.model_type, args.msa_mode,
                                             args.num_models, args.data_dir)
        print(f"Prediction succeeded: {directory}")
        print(f"Ranked PDB: {manifest['pdb']}; pLDDT mean: {manifest['plddt']['mean']:.2f}")
        return 0
    except (SequenceError, PredictionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
