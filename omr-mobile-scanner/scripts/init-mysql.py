#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'backend'))


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


load_env(BASE / '.env')

import storage  # noqa: E402


def main() -> int:
    if not storage.mysql_configured():
        print('OMR_MYSQL_DATABASE is not configured. Add it to .env first.', file=sys.stderr)
        return 2
    create_database = '--tables-only' not in sys.argv[1:]
    storage.initialise_mysql(BASE, create_database=create_database)
    print(f'MySQL database {storage.mysql_database_name()} and OMR tables are ready.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
