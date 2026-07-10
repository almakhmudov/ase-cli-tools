"""Typed job descriptions shared by every front-end (CLI, future GUI).

A config object fully describes a job, independent of how it was collected
(flags, interactive prompts, a GUI form, ...). Keeping this separate from the
Typer layer is what lets several front-ends drive the same run logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class JobConfig:
    """A job description. Which calculator and job skeleton are assembled is
    chosen by name from the registry, so this stays valid as new components are
    added (e.g. calculator='mace', job='npt', or job='singlepoint').

    It is a superset of the fields every job needs: an MD job reads the dynamics
    fields (temperature, timestep, thermostat, ...) and ignores ``sp_output``; a
    single-point job reads only the calculator + structure fields and ignores the
    dynamics ones. Fields a given job does not use simply are not rendered into
    its script (the registry lists each job's parameter names)."""

    checkpoint: Optional[str] = None     # calculator checkpoint (.pt); not all need one
    structure: Optional[str] = None      # starting structure / trajectory
    restart: Optional[str] = None        # restart trajectory (positions+velocities)

    # registry component selection. Every calculator is a variant family; the
    # variant selects the family member (MACE-MP, ...), the UMA task head (omol,
    # ...) or the GRACE model. None -> the calculator's default_variant.
    calculator: str = "uma"              # key in registry.CALCULATORS
    variant: Optional[str] = None        # variant key, e.g. "mace_mp" / "omol"
    job: str = "nvt"                     # key in registry.JOBS

    # Floating-point precision for MACE (default_dtype) and Orb (precision).
    # None -> use the calculator/variant default from the registry.
    precision: Optional[str] = None
    dispersion: bool = False         # MACE-MP / Orb-v3: D3 dispersion correction
    external_field: Optional[str] = None  # MACE-POLAR: "Ex Ey Ez" (optional)
    device: str = "auto"

    # ORCA (QM) calculator. orcasimpleinput is the "!" line; orcablocks is the
    # "% ... end" text (already newline-joined when several blocks are given).
    # nprocs, when set, is emitted as a leading "%pal nprocs N end" block, so the
    # user never hand-writes it. orca_command overrides the orca binary path via
    # an OrcaProfile (None -> ASE's configfile / PATH).
    orcasimpleinput: str = "B3LYP def2-SVP"
    orcablocks: Optional[str] = None
    nprocs: Optional[int] = None
    orca_command: Optional[str] = None

    cell: Optional[str] = None           # "a b c" | "a b c al be ga" | 9 values
    pbc: str = "true"
    charge: int = 0
    multiplicity: int = 1

    temperature: float = 298.15
    timestep: float = 0.5
    nsteps: int = 10000
    traj_interval: int = 10

    # Thermostat (thermostatted jobs only, e.g. NVT). None -> the job's default.
    # Each thermostat reads its own coupling parameters below; the others are
    # ignored. NVE has no thermostat, so these are unused there.
    thermostat: Optional[str] = None     # nose_hoover | langevin | csvr
    tdamp: Optional[float] = None        # Nose-Hoover coupling time (fs); None -> auto
    tchain: int = 3                      # Nose-Hoover chain length
    tloop: int = 1                       # Nose-Hoover inner loops
    friction: Optional[float] = None     # Langevin friction (fs^-1); None -> 0.01
    taut: Optional[float] = None         # CSVR coupling time (fs); None -> auto
    seed: int = 42

    # Geometry optimization (relax job). optimizer picks the ASE algorithm; None
    # -> the job's default. fmax is the convergence threshold: the run stops when
    # the max force on any atom falls below it (eV/A). nsteps caps the iterations.
    optimizer: Optional[str] = None      # bfgs | lbfgs | fire
    fmax: float = 0.05

    # Biasing (PLUMED)
    plumed: Optional[str] = None
    plumed_log: str = "plumed.log"
    prev_steps: Optional[int] = None

    # Output (MD)
    traj: str = "equil"
    traj_format: str = "traj"
    wrap: bool = False
    log: str = "equil.log"
    last_frame: str = "last_frame.xyz"

    # Output (single-point): results written as extended-xyz with a
    # SinglePointCalculator holding the requested properties. sp_forces adds
    # forces (and stress when periodic); leave it off for an energy-only run,
    # which is cheaper for QM codes.
    sp_forces: bool = True
    sp_output: str = "singlepoint.extxyz"


# Legacy name kept for the flag/wizard front-ends that import it.
NVTConfig = JobConfig


@dataclass
class WrapConfig:
    """A post-processing job that wraps every frame into the primary cell."""

    input: str
    output: Optional[str] = None
