#!/usr/bin/env python3
"""Start Uvicorn outside Plesk's short-lived scheduled-task process group."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid-file', required=True)
    parser.add_argument('--log-file', required=True)
    parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()

    first_child = os.fork()
    if first_child > 0:
        os.waitpid(first_child, 0)
        return 0

    os.setsid()
    second_child = os.fork()
    if second_child > 0:
        os._exit(0)

    pid_path = Path(args.pid_file)
    log_path = Path(args.log_file)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding='ascii')

    stdin_fd = os.open('/dev/null', os.O_RDONLY)
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(stdin_fd, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    if stdin_fd > 2:
        os.close(stdin_fd)
    if log_fd > 2:
        os.close(log_fd)

    os.execv(
        sys.executable,
        [
            sys.executable,
            '-m',
            'uvicorn',
            'backend.main:app',
            '--host',
            '127.0.0.1',
            '--port',
            str(args.port),
            '--proxy-headers',
        ],
    )


if __name__ == '__main__':
    raise SystemExit(main())
