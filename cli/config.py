"""Typed job descriptions shared by every front-end (CLI, future GUI).

A config object fully describes a job, independent of how it was collected
(flags, interactive prompts, a GUI form, ...). Keeping this separate from the
Typer layer is what lets several front-ends drive the same run logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


def _cell_values(cell: Optional[str]) -> List[str]:
    """Turn a cell string like '20 20 20' or '15,15,18,90,90,120' into tokens."""
    if not cell:
        return []
    return [tok for tok in cell.replace(",", " ").split() if tok]


@dataclass
class NVTConfig:
    """An MD job description. Which calculator and job skeleton are assembled is
    chosen by name from the registry, so this stays valid as new components are
    added (e.g. calculator='mace', job='npt')."""

    checkpoint: str                      # calculator checkpoint (.pt) -- required
    structure: Optional[str] = None      # starting structure / trajectory
    restart: Optional[str] = None        # restart trajectory (positions+velocities)

    # registry component selection
    calculator: str = "uma"              # key in registry.CALCULATORS
    job: str = "nvt"                     # key in registry.JOBS

    task_name: str = "omol"
    device: str = "auto"

    cell: Optional[str] = None           # "a b c" | "a b c al be ga" | 9 values
    pbc: str = "true"
    charge: int = 0
    multiplicity: int = 1

    temperature: float = 298.15
    timestep: float = 0.5
    nsteps: int = 10000
    traj_interval: int = 10
    tdamp: Optional[float] = None
    tchain: int = 3
    tloop: int = 1
    seed: int = 42

    # Biasing (PLUMED)
    plumed: Optional[str] = None
    plumed_log: str = "plumed.log"
    prev_steps: Optional[int] = None

    # Output
    traj: str = "equil"
    traj_format: str = "traj"
    wrap: bool = False
    log: str = "equil.log"
    last_frame: str = "last_frame.xyz"

    def to_argv(self) -> List[str]:
        """Render as an argv list for scripts/nvt_md_uma.py.

        This is the bridge to the existing, validated script. In the full
        refactor the script's run loop would move into ``core`` and both would
        consume this dataclass directly instead of via argv.
        """
        argv: List[str] = ["--checkpoint", self.checkpoint]
        if self.structure:
            argv += ["--structure", self.structure]
        if self.restart:
            argv += ["--restart", self.restart]
        argv += ["--task-name", self.task_name, "--device", self.device]
        if self.cell:
            argv.append("--cell")
            argv += _cell_values(self.cell)
        argv += ["--pbc", self.pbc]
        argv += ["--charge", str(self.charge)]
        argv += ["--multiplicity", str(self.multiplicity)]
        argv += ["--temperature", str(self.temperature)]
        argv += ["--timestep", str(self.timestep)]
        argv += ["--nsteps", str(self.nsteps)]
        argv += ["--traj-interval", str(self.traj_interval)]
        if self.tdamp is not None:
            argv += ["--tdamp", str(self.tdamp)]
        argv += ["--tchain", str(self.tchain), "--tloop", str(self.tloop)]
        argv += ["--seed", str(self.seed)]
        if self.plumed:
            argv += ["--plumed", self.plumed, "--plumed-log", self.plumed_log]
            if self.prev_steps is not None:
                argv += ["--prev-steps", str(self.prev_steps)]
        argv += ["--traj", self.traj, "--traj-format", self.traj_format]
        if self.wrap:
            argv.append("--wrap")
        argv += ["--log", self.log, "--last-frame", self.last_frame]
        return argv


@dataclass
class WrapConfig:
    """A post-processing job that wraps every frame into the primary cell."""

    input: str
    output: Optional[str] = None
