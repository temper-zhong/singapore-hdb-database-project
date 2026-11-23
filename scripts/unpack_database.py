"""Expand the shared MySQL dump without overwriting an existing SQL file."""

from pathlib import Path
import gzip
import hashlib
import json
import shutil


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / 'database/hdb_project.sql.gz'
    output = root / 'database/hdb_project.sql'
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite: {output}')
    with gzip.open(source, 'rb') as reader, output.open('xb') as writer:
        shutil.copyfileobj(reader, writer)
    manifest = json.loads((root / 'database/snapshot.json').read_text(encoding='utf-8'))
    actual = hashlib.sha256(output.read_bytes()).hexdigest()
    if actual != manifest['uncompressed_sha256']:
        raise RuntimeError('SQL checksum differs from the shared snapshot.')
    print(f'Extracted: {output}')
    print('Import into an empty hdb_project schema using MySQL Workbench.')


if __name__ == '__main__':
    main()
