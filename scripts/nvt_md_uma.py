#!/usr/bin/env python3
"""
NVT equilibration with a UMA (FairChem) machine-learned interatomic potential.

Example:

    python nvt_md_uma.py \
        --structure   mixture.xyz \
        --checkpoint   /path/to/uma-s-1p1.pt \
        --cell         20 20 20 \
        --pbc          true \
        --charge       0 \
        --multiplicity 2 \
        --temperature  298.15 \
        --timestep     0.5 \
        --nsteps       100000 \
        --traj-interval 10

Run `python nvt_md_uma.py --help` for the full list of options.
"""

import argparse
import os
import sys
import time

import numpy as np

from ase import units
from ase.io import read, write
from ase.io.trajectory import Trajectory
from ase.md.nose_hoover_chain import NoseHooverChainNVT
from ase.md.velocitydistribution import Stationary, ZeroRotation
try:
    # Renamed in ASE 3.29; MaxwellBoltzmannDistribution is deprecated.
    from ase.md.velocitydistribution import thermalize_momenta as set_momenta
except ImportError:  # ASE < 3.29
    from ase.md.velocitydistribution import (
        MaxwellBoltzmannDistribution as set_momenta,
    )

# amu -> kg ; Angstrom^3 -> m^3   (for a density in kg/m^3)
_AMU_KG = 1.66053906660e-27
_A3_M3 = 1.0e-30


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
EXAMPLES = """\
examples:
  # minimal run (GPU auto-detected), cell taken from the structure file
  python nvt_md_uma.py -s mixture.xyz -c /path/to/uma-s-1p1.pt

  # fully specified periodic box, open-shell system
  python nvt_md_uma.py \\
      --structure    mixture.xyz \\
      --checkpoint   /path/to/uma-s-1p2.pt \\
      --cell         20 20 20 \\
      --pbc          true \\
      --charge       0 \\
      --multiplicity 2 \\
      --temperature  498.15 \\
      --timestep     0.5 \\
      --nsteps       10000 \\
      --traj-interval 10

  # 5 ps run, write the trajectory as extxyz and also save a wrapped copy
  python nvt_md_uma.py -s mixture.xyz -c uma.pt \\
      --cell 20 20 20 --charge 0 --multiplicity 2 -T 498.15 \\
      -dt 0.5 -n 10000 --traj-interval 10 --traj-format xyz --wrap

  # continue a run: carry positions + velocities from a previous trajectory
  python nvt_md_uma.py --restart equil.traj -c uma.pt \\
      --cell 20 20 20 --charge 0 --multiplicity 2 -T 498.15 \\
      -dt 0.5 -n 20000 --traj-interval 10

  # biased NVT (metadynamics etc.): bias defined in plumed.dat
  python nvt_md_uma.py -s mixture.xyz -c uma.pt \\
      --cell 20 20 20 --charge 0 --multiplicity 2 -T 498.15 \\
      -dt 0.5 -n 500000 --traj-interval 50 --plumed plumed.dat

  # restart a biased run (append to HILLS, continue metadynamics time)
  python nvt_md_uma.py --restart equil.traj -c uma.pt \\
      --cell 20 20 20 --charge 0 --multiplicity 2 -T 498.15 \\
      -dt 0.5 -n 500000 --traj-interval 50 --plumed plumed.dat \\
      --prev-steps 500000

  # non-orthorhombic cell (a b c alpha beta gamma) and a custom thermostat
  python nvt_md_uma.py -s box.cif -c uma.pt \\
      --cell 15 15 18 90 90 120 --tdamp 50 --tchain 3

PLUMED input note:
  Each PLUMED command or definition MUST be written on a SINGLE line. The
  Python PLUMED interface reads the file line by line, so a command split over
  several lines (e.g. with a trailing backslash or a line break inside its
  arguments) will crash with a parse error. Comment lines ('#') are stripped
  automatically, but line continuations are NOT supported - keep every action
  on one line.
"""


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter,
                     argparse.RawDescriptionHelpFormatter):
    """Show argument defaults *and* keep the epilog's literal formatting."""


def parse_pbc(value):
    """Parse the --pbc argument into a 3-tuple of bools.

    Accepts:  true/false/1/0/yes/no  -> applied to all three axes
              three tokens (e.g. "1 1 0" or "T T F") -> per-axis
    """
    truthy = {"true", "t", "1", "yes", "y", "on"}
    falsy = {"false", "f", "0", "no", "n", "off"}

    def to_bool(tok):
        t = tok.strip().lower()
        if t in truthy:
            return True
        if t in falsy:
            return False
        raise argparse.ArgumentTypeError(f"cannot interpret '{tok}' as a boolean")

    toks = value.replace(",", " ").split()
    if len(toks) == 1:
        b = to_bool(toks[0])
        return (b, b, b)
    if len(toks) == 3:
        return tuple(to_bool(t) for t in toks)
    raise argparse.ArgumentTypeError("--pbc expects either 1 or 3 values")


def build_parser():
    p = argparse.ArgumentParser(
        description="NVT equilibration with a UMA potential and a "
                    "Nose-Hoover chain thermostat.",
        epilog=EXAMPLES,
        formatter_class=_HelpFormatter,
    )

    # ---- System ----------------------------------------------------------- #
    g_sys = p.add_argument_group("system")
    g_sys.add_argument("--structure", "-s", default=None,
                       help="Starting structure (any ASE-readable format: "
                            ".xyz, .extxyz, .pdb, .cif, ...). Required unless "
                            "--restart is given.")
    g_sys.add_argument("--restart", "-r", default=None,
                       help="Restart from the LAST frame of a previous "
                            "trajectory (.traj or .xyz): positions AND velocities "
                            "are carried over, so no new velocities are drawn and "
                            "--structure is not needed. Mutually exclusive with "
                            "--structure.")
    g_sys.add_argument("--cell", type=float, nargs="+", default=None,
                       help="Cell parameters. Give 3 values (a b c, orthorhombic), "
                            "6 values (a b c alpha beta gamma) or 9 values (full "
                            "3x3 matrix, row-major). If omitted the cell from the "
                            "structure file is used.")
    g_sys.add_argument("--pbc", type=parse_pbc, default=(True, True, True),
                       help="Periodicity: one value (applied to all axes) or three "
                            "(per axis). Accepts true/false, 1/0, T/F.")
    g_sys.add_argument("--charge", type=int, default=0,
                       help="Total charge (used by the UMA omol task).")
    g_sys.add_argument("--multiplicity", type=int, default=1,
                       help="Spin multiplicity 2S+1 (used by the UMA omol task).")

    # ---- Model / device --------------------------------------------------- #
    g_mod = p.add_argument_group("model")
    g_mod.add_argument("--checkpoint", "-c", required=True,
                       help="Path to the downloaded UMA checkpoint (.pt).")
    g_mod.add_argument("--task-name", default="omol",
                       choices=["omol", "omat", "omc", "oc20", "odac"],
                       help="UMA task head. 'omol' uses charge & multiplicity.")
    g_mod.add_argument("--device", default="auto",
                       choices=["auto", "cuda", "cpu"],
                       help="Compute device. 'auto' picks CUDA if available.")

    # ---- Thermostat / integration ---------------------------------------- #
    g_md = p.add_argument_group("dynamics")
    g_md.add_argument("--temperature", "-T", type=float, default=298.15,
                      help="Target temperature in K.")
    g_md.add_argument("--timestep", "-dt", type=float, default=0.5,
                      help="Integration timestep in fs.")
    g_md.add_argument("--nsteps", "-n", type=int, default=100000,
                      help="Number of MD steps to run.")
    g_md.add_argument("--traj-interval", type=int, default=10,
                      help="Record a trajectory frame (and a log line) every N steps.")
    g_md.add_argument("--tdamp", type=float, default=None,
                      help="Nose-Hoover coupling time in fs. Default: 100*timestep "
                           "(and at least 20 fs).")
    g_md.add_argument("--tchain", type=int, default=3,
                      help="Length of the Nose-Hoover chain.")
    g_md.add_argument("--tloop", type=int, default=1,
                      help="Number of inner integration loops of the thermostat.")
    g_md.add_argument("--seed", type=int, default=42,
                      help="RNG seed for the initial Maxwell-Boltzmann velocities.")

    # ---- Biasing (PLUMED) ------------------------------------------------- #
    g_bias = p.add_argument_group("biasing (PLUMED)")
    g_bias.add_argument("--plumed", default=None,
                        help="Path to a PLUMED input file (e.g. plumed.dat). If "
                             "given, the run is biased: the UMA calculator is "
                             "wrapped in ASE's Plumed calculator, which adds the "
                             "bias forces defined in the file. The file's own "
                             "UNITS line controls PLUMED-side I/O units.")
    g_bias.add_argument("--plumed-log", default="plumed.log",
                        help="Log file for PLUMED output.")
    g_bias.add_argument("--prev-steps", type=int, default=None,
                        help="Biased RESTART only: number of MD steps already "
                             "performed in the previous run. Sets PLUMED's step "
                             "counter (istep) so metadynamics time and HILLS "
                             "deposition continue correctly. Ignored otherwise.")

    # ---- Output ----------------------------------------------------------- #
    g_out = p.add_argument_group("output")
    g_out.add_argument("--traj", default="equil",
                       help="Base name (or path) for the trajectory. The "
                            "extension is set automatically from --traj-format.")
    g_out.add_argument("--traj-format", default="traj", choices=["traj", "xyz"],
                       help="Trajectory format for both the unwrapped and "
                            "wrapped files: 'traj' (ASE binary) or 'xyz' (extxyz).")
    g_out.add_argument("--wrap", action="store_true",
                       help="After the run, also write a wrapped trajectory "
                            "(all atoms folded into the primary cell) as "
                            "'<traj>_wrapped.<ext>'. The unwrapped trajectory is "
                            "kept as well.")
    g_out.add_argument("--log", default="equil.log",
                       help="Output text log file.")
    g_out.add_argument("--last-frame", default="last_frame.xyz",
                       help="File for the final frame (extxyz format).")

    return p


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def resolve_device(choice):
    """Pick 'cuda' or 'cpu'. 'auto' -> cuda if a GPU is visible."""
    import torch
    if choice == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if choice == "cuda" and not torch.cuda.is_available():
        print("WARNING: --device cuda requested but no CUDA device is visible; "
              "falling back to CPU.", flush=True)
        return "cpu"
    return choice


def apply_cell(atoms, cell_arg):
    """Apply the --cell argument (3, 6 or 9 numbers) to the atoms object."""
    n = len(cell_arg)
    if n == 3:
        atoms.set_cell(cell_arg)          # orthorhombic a, b, c
    elif n == 6:
        atoms.set_cell(cell_arg)          # a, b, c, alpha, beta, gamma
    elif n == 9:
        atoms.set_cell(np.array(cell_arg, float).reshape(3, 3))
    else:
        raise SystemExit(f"--cell expects 3, 6 or 9 numbers (got {n}).")


def density_kg_m3(atoms):
    """Mass density of the (periodic) cell in kg/m^3."""
    volume = atoms.get_volume()           # Angstrom^3
    if volume <= 0.0:
        return float("nan")
    mass = atoms.get_masses().sum()       # amu
    return (mass * _AMU_KG) / (volume * _A3_M3)


def backup_if_exists(path):
    """Rename an existing file so its content is preserved, never overwritten.

    `equil.log` -> `equil_backup1.log` (then `_backup2`, ... for the next
    lowest index that does not already exist). Returns the backup path, or
    None if there was nothing to back up.
    """
    if not os.path.exists(path):
        return None
    root, ext = os.path.splitext(path)
    i = 1
    while True:
        backup = f"{root}_backup{i}{ext}"
        if not os.path.exists(backup):
            break
        i += 1
    os.rename(path, backup)
    print(f"Existing '{path}' found -> renamed to '{backup}'", flush=True)
    return backup


# Map the CLI --traj-format choice to (file extension, ASE format string).
_TRAJ_FORMATS = {"traj": ("traj", "traj"), "xyz": ("xyz", "extxyz")}


def make_trajectory_writer(path, traj_format, atoms):
    """Return (write_fn, close_fn) that append one frame of `atoms` per call.

    'traj' uses ASE's binary Trajectory; 'xyz' streams frames to a single
    extxyz file through one open handle (so the file is not reopened per frame).
    """
    if traj_format == "traj":
        traj = Trajectory(path, "w", atoms)
        return traj.write, traj.close

    fh = open(path, "w")

    def write_fn():
        write(fh, atoms, format="extxyz")

    return write_fn, fh.close


def wrap_trajectory(src, dst, ase_format):
    """Read every frame of `src`, wrap it into the cell, stream to `dst`.

    Frames are appended one at a time (via filename) so this works for both
    the binary 'traj' and text 'extxyz' formats.
    """
    from ase.io import iread
    n = 0
    for frame in iread(src):
        frame.wrap()                      # uses each frame's cell + pbc
        write(dst, frame, format=ase_format, append=(n > 0))
        n += 1
    return n


def load_plumed_input(path):
    """Read a PLUMED input file, stripping comments and blank lines.

    PLUMED's line-by-line reader (`readInputLine`, used by ASE) rejects comment
    lines, so a stray '#' line aborts the run. Here we drop full-line and inline
    '#' comments and any resulting blank lines. Returns (clean_lines, removed),
    where `removed` is the number of comment/blank lines discarded.
    """
    clean, removed = [], 0
    with open(path) as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].rstrip()     # drop inline/full comments
            if line.strip():
                clean.append(line)
            else:
                removed += 1
    return clean, removed


def plumed_output_files(setup_lines):
    """Return the filenames referenced by `FILE=...` in a PLUMED input.

    Covers e.g. `METAD ... FILE=HILLS` and `PRINT ... FILE=COLVAR`. Comments
    (starting with '#') are ignored. Order-preserving and de-duplicated.
    """
    files = []
    for line in setup_lines:
        line = line.split("#", 1)[0]                 # drop inline comments
        for tok in line.split():
            if tok.upper().startswith("FILE="):
                name = tok.split("=", 1)[1].strip()
                if name and name not in files:
                    files.append(name)
    return files


def format_hms(seconds):
    """Seconds -> 'HH:MM:SS' (or '--:--:--' when unknown)."""
    if seconds is None or not np.isfinite(seconds):
        return "--:--:--"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class MDReporter:
    """Writes the per-frame log line and prints a matching line to stdout.

    Columns: frame, time_fs, E_pot, E_kin, E_tot (eV), density (kg/m^3),
    temperature (K) and the predicted wall-clock time remaining.
    """

    HEADER = (
        "# {:>8s} {:>14s} {:>18s} {:>18s} {:>18s} {:>14s} {:>10s} {:>12s}\n".format(
            "frame", "time_fs", "E_pot_eV", "E_kin_eV", "E_tot_eV",
            "rho_kg_m3", "T_K", "ETA_hh:mm:ss")
    )

    def __init__(self, dyn, atoms, log_path, dt_fs, total_steps, traj_interval):
        self.dyn = dyn
        self.atoms = atoms
        self.dt_fs = dt_fs
        self.total_steps = total_steps
        self.traj_interval = max(1, traj_interval)
        self.n_dof = 3 * len(atoms)        # used only for a diagnostic T
        self.frame = 0
        self.t_start = None
        self.fh = open(log_path, "w")
        self.fh.write(self.HEADER)
        self.fh.flush()

    def __call__(self):
        if self.t_start is None:
            self.t_start = time.time()

        step = self.dyn.get_number_of_steps()
        t_fs = step * self.dt_fs
        epot = self.atoms.get_potential_energy()
        ekin = self.atoms.get_kinetic_energy()
        etot = epot + ekin
        temp = self.atoms.get_temperature()
        rho = density_kg_m3(self.atoms)

        # ETA from the mean wall-time per completed step so far.
        elapsed = time.time() - self.t_start
        if step > 0:
            per_step = elapsed / step
            eta = per_step * max(self.total_steps - step, 0)
        else:
            eta = None

        line = ("{:>10d} {:>14.2f} {:>18.6f} {:>18.6f} {:>18.6f} "
                "{:>14.3f} {:>10.2f} {:>12s}").format(
            self.frame, t_fs, epot, ekin, etot, rho, temp, format_hms(eta))
        self.fh.write(line + "\n")
        self.fh.flush()

        # Mirror the log file on stdout (same header + columns, no 'step').
        if self.frame == 0:
            print(self.HEADER.rstrip("\n"), flush=True)
        print(line, flush=True)

        self.frame += 1

    def close(self):
        self.fh.close()


def write_last_frame(atoms, path, step, time_fs, energy):
    """Write the final configuration as extxyz with a minimal comment line.

    Keeps only: Lattice, Properties (species + pos), step, time_fs, energy, pbc.
    Momenta, forces and stress are deliberately omitted.
    """
    from ase.calculators.singlepoint import SinglePointCalculator

    out = atoms.copy()                       # copy drops the live calculator
    out.arrays.pop("momenta", None)          # no velocities -> no momenta column
    out.info = {"step": step, "time_fs": time_fs}
    # A bare single-point calc so only `energy` (no forces/stress) is emitted.
    out.calc = SinglePointCalculator(out, energy=energy)
    write(path, out, format="extxyz")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate the structure/restart choice up front, before any heavy imports.
    if bool(args.structure) == bool(args.restart):
        parser.error("provide exactly one of --structure/-s or --restart/-r.")

    # ---- Device ----------------------------------------------------------- #
    device = resolve_device(args.device)
    print(f"Using device: {device}", flush=True)

    # ---- Load structure (fresh start or restart) ------------------------- #
    restart = args.restart is not None
    source = args.restart if restart else args.structure
    if not os.path.isfile(source):
        raise SystemExit(f"{'Restart' if restart else 'Structure'} file "
                         f"not found: {source}")
    # Always take the LAST frame of the file. For a restart, both positions and
    # velocities carry over. For a fresh start from a multi-frame structure file
    # (e.g. a trajectory), only the geometry is used; velocities are re-drawn.
    atoms = read(source, index=-1)
    if not restart:
        multiframe = False
        try:
            read(source, index=1)            # succeeds only if >= 2 frames
            multiframe = True
        except (StopIteration, IndexError):
            multiframe = False
        if multiframe:
            print(f"Structure file '{source}' contains multiple frames: using "
                  f"its LAST frame as the starting configuration (fresh "
                  f"velocities will be drawn, as this is not a restart).",
                  flush=True)

    if args.cell is not None:
        apply_cell(atoms, args.cell)
        cell_source = "CLI (--cell)"
    else:
        cell_source = f"file ({source})"
    atoms.set_pbc(args.pbc)

    periodic = bool(np.any(atoms.pbc))
    if periodic and atoms.cell.rank < 3:
        raise SystemExit("System is periodic but the cell is not fully 3D. "
                         "Provide --cell.")

    # Charge / multiplicity for the UMA omol task.
    atoms.info["charge"] = args.charge
    atoms.info["spin"] = args.multiplicity

    print(f"{len(atoms)} atoms | cell from {cell_source} | pbc={tuple(atoms.pbc)}",
          flush=True)
    print(f"cell lengths = {atoms.cell.lengths()}", flush=True)
    if periodic:
        print(f"initial density = {density_kg_m3(atoms):.2f} kg/m3", flush=True)
    else:
        print("WARNING: system is not periodic; density will be NaN.", flush=True)

    # ---- UMA calculator --------------------------------------------------- #
    if not os.path.isfile(args.checkpoint):
        raise SystemExit(f"UMA checkpoint not found: {args.checkpoint}")

    try:
        from fairchem.core import FAIRChemCalculator
    except ImportError:
        from fairchem.core.calculate.ase_calculator import FAIRChemCalculator
    from fairchem.core.units.mlip_unit import load_predict_unit

    predictor = load_predict_unit(args.checkpoint, device=device)
    base_calc = FAIRChemCalculator(predictor, task_name=args.task_name)
    print(f"Loaded UMA checkpoint '{args.checkpoint}' "
          f"(task={args.task_name}, device={device}).", flush=True)

    # ---- Optional PLUMED bias -------------------------------------------- #
    # When --plumed is given, wrap the UMA calculator so PLUMED adds its bias
    # forces on top of the UMA forces (e.g. metadynamics, walls, ...).
    if args.plumed:
        if not os.path.isfile(args.plumed):
            raise SystemExit(f"PLUMED input file not found: {args.plumed}")
        from ase.calculators.plumed import Plumed

        # PLUMED's line reader cannot parse '#' comment lines, so strip them.
        plumed_setup, n_removed = load_plumed_input(args.plumed)
        if n_removed:
            root, ext = os.path.splitext(args.plumed)
            parsed_path = f"{root}_parsed{ext or '.dat'}"
            with open(parsed_path, "w") as fh:
                fh.write("\n".join(plumed_setup) + "\n")
            print(f"PLUMED input: removed {n_removed} comment/blank line(s) that "
                  f"PLUMED's line reader cannot parse. The exact input passed to "
                  f"PLUMED was written to '{parsed_path}' for reference.",
                  flush=True)

        # On a biased restart, the previous PLUMED output files must be present
        # in the working directory (metadynamics reads HILLS to rebuild the
        # bias). Abort early if they are missing.
        if restart:
            referenced = plumed_output_files(plumed_setup) or ["HILLS", "COLVAR"]
            missing = [f for f in referenced if not os.path.isfile(f)]
            if missing:
                raise SystemExit(
                    "Biased restart aborted: required PLUMED file(s) not found "
                    "in the working directory: " + ", ".join(missing) + ". "
                    "A metadynamics restart needs the previous HILLS (and "
                    "COLVAR) files. Run from the original directory, or copy "
                    "them here.")
            print(f"Biased restart: found PLUMED file(s) "
                  f"{', '.join(referenced)}.", flush=True)

        kT_eV = units.kB * args.temperature
        atoms.calc = Plumed(
            calc=base_calc,
            input=plumed_setup,
            timestep=args.timestep * units.fs,   # ASE time units, matches integrator
            atoms=atoms,
            kT=kT_eV,                             # thermal energy in eV
            log=args.plumed_log,
            restart=restart,                      # append to HILLS on restart
        )
        msg = (f"Biased run: PLUMED input '{args.plumed}', "
               f"kT={kT_eV:.5f} eV, log '{args.plumed_log}'.")
        if restart:
            if args.prev_steps is not None:
                atoms.calc.istep = args.prev_steps
                msg += f" Restart: PLUMED istep set to {args.prev_steps}."
            else:
                msg += (" WARNING: biased restart without --prev-steps; PLUMED "
                        "istep stays 0, so metadynamics time will not be "
                        "continuous with the previous run.")
        print(msg, flush=True)
    else:
        atoms.calc = base_calc

    # ---- Initial velocities ---------------------------------------------- #
    if restart and np.any(atoms.get_momenta()):
        # Continue seamlessly from the previous run's velocities.
        print(f"Restart: positions + velocities taken from the last frame of "
              f"'{source}' (T = {atoms.get_temperature():.1f} K).", flush=True)
    else:
        if restart:
            print("WARNING: restart file has no velocities; drawing fresh "
                  "Maxwell-Boltzmann momenta instead.", flush=True)
        set_momenta(atoms, temperature_K=args.temperature,
                    rng=np.random.default_rng(args.seed))
        Stationary(atoms)             # remove net centre-of-mass translation
        if not periodic:
            ZeroRotation(atoms)       # only meaningful for an isolated molecule

    # ---- Thermostat ------------------------------------------------------ #
    # Nose-Hoover coupling time (tdamp): a good default is ~100 timesteps.
    if args.tdamp is None:
        tdamp_fs = max(100.0 * args.timestep, 20.0)
    else:
        tdamp_fs = args.tdamp
    print(f"Nose-Hoover: T={args.temperature} K, dt={args.timestep} fs, "
          f"tdamp={tdamp_fs} fs, tchain={args.tchain}, tloop={args.tloop}",
          flush=True)

    dyn = NoseHooverChainNVT(
        atoms,
        timestep=args.timestep * units.fs,
        temperature_K=args.temperature,
        tdamp=tdamp_fs * units.fs,
        tchain=args.tchain,
        tloop=args.tloop,
    )

    # ---- Resolve trajectory paths ---------------------------------------- #
    # The extension follows --traj-format; --traj supplies only the base name.
    ext, ase_format = _TRAJ_FORMATS[args.traj_format]
    traj_stem = os.path.splitext(args.traj)[0]
    traj_path = f"{traj_stem}.{ext}"
    wrapped_path = f"{traj_stem}_wrapped.{ext}"

    # ---- Preserve any pre-existing output files --------------------------- #
    # Back them up before writing so a previous run's data is never clobbered.
    backup_targets = [traj_path, args.log, args.last_frame]
    if args.wrap:
        backup_targets.append(wrapped_path)
    for out_path in backup_targets:
        backup_if_exists(out_path)

    # ---- Attach outputs --------------------------------------------------- #
    traj_write, traj_close = make_trajectory_writer(traj_path, args.traj_format,
                                                    atoms)
    dyn.attach(traj_write, interval=args.traj_interval)

    reporter = MDReporter(dyn, atoms, args.log, args.timestep,
                          total_steps=args.nsteps,
                          traj_interval=args.traj_interval)
    dyn.attach(reporter, interval=args.traj_interval)

    # ---- Run -------------------------------------------------------------- #
    # Attached observers fire once at step 0, so the initial state is logged
    # and written as frame 0 automatically.
    t0 = time.time()
    dyn.run(args.nsteps)
    wall = time.time() - t0
    reporter.close()
    traj_close()

    print(f"[nvt] {args.nsteps} steps in {wall:.1f} s "
          f"({1000 * wall / max(args.nsteps, 1):.2f} ms/step) -> {traj_path}",
          flush=True)

    # ---- Optional wrapped trajectory ------------------------------------- #
    if args.wrap:
        n = wrap_trajectory(traj_path, wrapped_path, ase_format)
        print(f"[nvt] wrapped {n} frames -> {wrapped_path}", flush=True)

    # ---- Final frame as extxyz ------------------------------------------- #
    epot = atoms.get_potential_energy()
    write_last_frame(atoms, args.last_frame,
                     step=args.nsteps,
                     time_fs=args.nsteps * args.timestep,
                     energy=epot)
    print(f"[nvt] wrote final frame -> {args.last_frame}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
