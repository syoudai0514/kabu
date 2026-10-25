"""Recover only an interrupted final JSONL write, retaining the discarded bytes."""
import hashlib
import json
import pathlib


def load_jsonl(path):
    path = pathlib.Path(path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    records = []
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            if index != len(lines)-1 or line.endswith(b'\n'):
                raise ValueError('Corrupt complete checkpoint record; manual investigation required')
            backup = path.with_name(path.name + '.incomplete-' + hashlib.sha256(line).hexdigest() + '.bin')
            if not backup.exists():
                backup.write_bytes(line)
            repaired = b''.join(lines[:index])
            break
        if not isinstance(record, dict):
            raise ValueError('Checkpoint record must be an object')
        records.append(record)
    else:
        # A fully written record without its terminating newline is not discarded.
        repaired = raw + b'\n' if raw and not raw.endswith(b'\n') else raw
    if repaired != raw:
        temporary = path.with_name(path.name + '.repair.tmp')
        temporary.write_bytes(repaired)
        temporary.replace(path)
    return records
