"""The work layer: functions that take a config and do the job.

No argument parsing and no prompting happens here -- that belongs to the
front-end. Every function takes a typed config from ``config.py``.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

from .config import NVTConfig, WrapConfig

# Repo root = parent of this package directory.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NVT_SCRIPT = os.path.join(_REPO_ROOT, "scripts", "nvt_md_uma.py")


def _load_nvt_module():
    """Import scripts/nvt_md_uma.py as a module (it guards __main__)."""
    spec = importlib.util.spec_from_file_location("nvt_md_uma", _NVT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_nvt(cfg: NVTConfig) -> None:
    """Run a (possibly biased) UMA NVT job described by `cfg`."""
    module = _load_nvt_module()
    module.main(cfg.to_argv())


def execute_script(path: str) -> None:
    """Run a generated script with the current Python interpreter.

    Running the exact file we generated guarantees that what executes is what
    gets published.
    """
    subprocess.run([sys.executable, path], check=True)


def wrap_trajectory(cfg: WrapConfig):
    """Wrap every frame of a trajectory into the primary cell.

    Returns (output_path, n_frames).
    """
    from ase.io import read, write

    frames = read(cfg.input, index=":")
    for atoms in frames:
        atoms.wrap()
    out = cfg.output or (os.path.splitext(cfg.input)[0] + "_wrapped.extxyz")
    write(out, frames)
    return out, len(frames)
