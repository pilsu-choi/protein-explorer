"""Parsing and validation for the M0 single-protein input contract."""

from pathlib import Path

AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
MIN_LENGTH = 30
MAX_LENGTH = 300


class SequenceError(ValueError):
    """An input is not one valid sequence in the supported length range."""


def parse_sequence(text: str) -> str:
    """Normalize raw sequence or one FASTA record; return uppercase residues."""
    lines = text.splitlines()
    if not lines:
        raise SequenceError("No sequence was provided.")

    headers = [i for i, line in enumerate(lines) if line.lstrip().startswith(">")]
    if headers:
        if len(headers) != 1:
            raise SequenceError("Provide exactly one FASTA record; multiple sequences are not supported.")
        first_nonempty = next((i for i, line in enumerate(lines) if line.strip()), None)
        if headers[0] != first_nonempty:
            raise SequenceError("FASTA header must be the first non-empty line.")
        sequence_lines = lines[headers[0] + 1 :]
    else:
        sequence_lines = lines

    sequence = "".join("".join(line.split()) for line in sequence_lines).upper()
    if not sequence:
        raise SequenceError("No amino-acid sequence follows the input header.")
    invalid = sorted(set(sequence) - AMINO_ACIDS)
    if invalid:
        raise SequenceError(f"Unsupported sequence character(s): {', '.join(repr(c) for c in invalid)}. Use the 20 standard amino acids.")
    if not MIN_LENGTH <= len(sequence) <= MAX_LENGTH:
        raise SequenceError(f"Sequence length is {len(sequence)} residues; supported range is {MIN_LENGTH}-{MAX_LENGTH}.")
    return sequence


def read_sequence(value: str | None, file_path: str | None) -> str:
    if (value is None) == (file_path is None):
        raise SequenceError("Provide either a sequence argument or --file PATH.")
    if file_path is not None:
        path = Path(file_path)
        try:
            contents = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SequenceError(f"Cannot read sequence file {path}: {exc}") from exc
        return parse_sequence(contents)
    return parse_sequence(value or "")
