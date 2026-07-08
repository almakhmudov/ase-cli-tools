"""The work layer: functions that take a config and do the job.

No argument parsing and no prompting happens here -- that belongs to the
front-end. Every function takes a typed config from ``config.py``.
"""

from __future__ import annotations

import subprocess
import sys


def execute_script(path: str) -> None:
    """Run a generated script with the current Python interpreter.

    Running the exact file we generated guarantees that what executes is what
    gets published.
    """
    subprocess.run([sys.executable, path], check=True)
