from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> None:
    log_dir = ROOT / "outputs"
    log_dir.mkdir(exist_ok=True)
    stdout = (log_dir / "server.out.log").open("ab")
    stderr = (log_dir / "server.err.log").open("ab")
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "travel_agent.api",
            "--host",
            "127.0.0.1",
            "--port",
            "8001",
        ],
        cwd=ROOT,
        stdout=stdout,
        stderr=stderr,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


if __name__ == "__main__":
    main()
