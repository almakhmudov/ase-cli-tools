"""Typed job descriptions shared by every front-end (CLI, future GUI).

A config object fully describes a job, independent of how it was collected
(flags, interactive prompts, a GUI form, ...). Keeping this separate from the
Typer layer is what lets several front-ends drive the same run logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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
    variant: Optional[str] = None        # calculator variant, e.g. "mace_mp"
    job: str = "nvt"                     # key in registry.JOBS

    task_name: str = "omol"          # UMA task head (unused by other calculators)
    # Floating-point precision for MACE (default_dtype) and Orb (precision).
    # None -> use the calculator/variant default from the registry.
    precision: Optional[str] = None
    dispersion: bool = False         # MACE-MP / Orb-v3: D3 dispersion correction
    external_field: Optional[str] = None  # MACE-POLAR: "Ex Ey Ez" (optional)
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


@dataclass
class WrapConfig:
    """A post-processing job that wraps every frame into the primary cell."""

    input: str
    output: Optional[str] = None
