"""Typer front-end: `ase-cli-tools <category> <job> [options]`.

The tool GENERATES standalone, runnable ASE scripts (with parameters baked in)
so that jobs are reproducible and publishable. It can optionally run the script
it just wrote.

Two ways to use it, both requested:
  * flag-driven (scriptable):        ase-cli-tools md run --job nvt -c uma.pt -s in.xyz ...
  * interactive arrow-key wizard:    ase-cli-tools        (no arguments)

Categories
----------
  md           molecular dynamics jobs   (--job nvt, ...); --plumed adds biasing
  postprocess  post-processing tasks     (wrap)
"""

from __future__ import annotations

from typing import List, Optional

import typer

from .config import NVTConfig, WrapConfig
from . import core, generate, registry

app = typer.Typer(
    invoke_without_command=True,   # allow the no-args wizard
    add_completion=True,
    help="Generate (and optionally run) reproducible ASE job scripts.",
)
md_app = typer.Typer(no_args_is_help=True, help="Molecular dynamics jobs.")
sp_app = typer.Typer(no_args_is_help=True, help="Single-point calculations.")
relax_app = typer.Typer(no_args_is_help=True, help="Geometry optimization.")
post_app = typer.Typer(no_args_is_help=True, help="Post-processing tasks.")
app.add_typer(md_app, name="md")
app.add_typer(sp_app, name="sp")
app.add_typer(relax_app, name="relax")
app.add_typer(post_app, name="postprocess")


@app.callback()
def _main(ctx: typer.Context):
    """Launch the interactive wizard when no sub-command is given."""
    if ctx.invoked_subcommand is None:
        run_wizard()


# --------------------------------------------------------------------------- #
# Shared: emit a script, optionally print/run it
# --------------------------------------------------------------------------- #
_CALCULATORS = list(registry.CALCULATORS)   # e.g. ["uma"], grows with the registry
_THERMOSTATS = list(registry.THERMOSTATS)    # e.g. ["nose_hoover", "langevin", ...]
_OPTIMIZERS = list(registry.OPTIMIZERS)      # ["bfgs", "lbfgs", "fire"]

_TRUE = {"true", "t", "1", "yes", "y", "on"}
_FALSE = {"false", "f", "0", "no", "n", "off"}


def _parse_bool(value: str, flag: str) -> bool:
    """Parse a True/False option value, erroring on anything else."""
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise typer.BadParameter(f"{flag} expects True or False (got {value!r}).")


def _check_calc(cfg: NVTConfig):
    """Validate the calculator selection common to every job and warn about
    options that do not apply to the chosen calculator/variant. Returns the
    resolved component spec."""
    if cfg.calculator not in registry.CALCULATORS:
        raise typer.BadParameter(f"unknown calculator {cfg.calculator!r}; "
                                 f"choose from {_CALCULATORS}.")
    try:
        _, comp = registry.resolve_variant(cfg.calculator, cfg.variant)
    except KeyError as exc:
        raise typer.BadParameter(str(exc))

    # A checkpoint is required only for components that take one.
    if "CHECKPOINT" in comp["params"] and not cfg.checkpoint:
        raise typer.BadParameter("a calculator --checkpoint is required.")

    # Validate --precision against the chosen variant's allowed values.
    pspec = comp.get("precision")
    if pspec and cfg.precision is not None and cfg.precision not in pspec["choices"]:
        raise typer.BadParameter(
            f"unknown precision {cfg.precision!r} for "
            f"{cfg.variant or cfg.calculator!r}; choose from {pspec['choices']}.")

    # Warn when charge/multiplicity are set but the chosen variant does not use
    # them.
    if (not registry.uses_charge_spin(cfg.calculator, cfg.variant)
            and (cfg.charge != 0 or cfg.multiplicity != 1)):
        typer.secho("Note: --charge/--multiplicity are only used by ORCA, UMA's "
                    "'omol' task, MACE-POLAR and OrbMol-v2; ignoring them here.",
                    fg=typer.colors.YELLOW)
    if cfg.external_field and "EXTERNAL_FIELD" not in comp["params"]:
        typer.secho("Note: --external-field applies only to MACE-POLAR; "
                    "ignoring it here.", fg=typer.colors.YELLOW)

    # ORCA-specific flags: warn when used with a non-ORCA calculator, and remind
    # ORCA users that dispersion goes in --orcasimpleinput (e.g. 'D3BJ'), not the
    # MLIP --dispersion flag.
    is_orca = cfg.calculator == "orca"
    if not is_orca and (cfg.orcablocks or cfg.nprocs or cfg.orca_command):
        typer.secho("Note: --orcablock/--nprocs/--orca-command apply only to the "
                    "ORCA calculator; ignoring them here.", fg=typer.colors.YELLOW)
    if is_orca and cfg.dispersion:
        typer.secho("Note: for ORCA, add dispersion to --orcasimpleinput (e.g. "
                    "'B3LYP def2-SVP D3BJ'); --dispersion is ignored here.",
                    fg=typer.colors.YELLOW)
    return comp


def _emit_nvt(cfg: NVTConfig, output: str, to_stdout: bool, run: bool) -> None:
    if bool(cfg.structure) == bool(cfg.restart):
        raise typer.BadParameter("provide exactly one of --structure or --restart.")

    comp = _check_calc(cfg)

    # Validate --thermostat against the chosen job (thermostatted jobs only) and
    # warn when a coupling flag does not match the active thermostat.
    try:
        thermo_name, _ = registry.resolve_thermostat(cfg.job, cfg.thermostat)
    except KeyError as exc:
        raise typer.BadParameter(str(exc))
    if not registry.JOBS[cfg.job].get("thermostats"):
        if any(v is not None for v in (cfg.thermostat, cfg.friction, cfg.taut,
                                       cfg.tdamp)):
            typer.secho(f"Note: job {cfg.job!r} has no thermostat; ignoring "
                        "thermostat options.", fg=typer.colors.YELLOW)
    else:
        if cfg.friction is not None and thermo_name != "langevin":
            typer.secho("Note: --friction applies only to the Langevin "
                        "thermostat; ignoring it here.", fg=typer.colors.YELLOW)
        if cfg.taut is not None and thermo_name != "csvr":
            typer.secho("Note: --taut applies only to the CSVR thermostat; "
                        "ignoring it here.", fg=typer.colors.YELLOW)
        if cfg.tdamp is not None and thermo_name != "nose_hoover":
            typer.secho("Note: --tdamp applies only to the Nose-Hoover "
                        "thermostat; ignoring it here.", fg=typer.colors.YELLOW)

    text = generate.generate_md_script(cfg, script_name=output)
    if to_stdout:
        typer.echo(text)
        return
    path = generate.write_script(text, output)
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)
    typer.echo(f"Run it with:  python {path}")
    if run:
        core.execute_script(path)


def _emit_sp(cfg: NVTConfig, output: str, to_stdout: bool, run: bool) -> None:
    if not cfg.structure:
        raise typer.BadParameter("a --structure is required.")
    _check_calc(cfg)
    text = generate.generate_singlepoint_script(cfg, script_name=output)
    if to_stdout:
        typer.echo(text)
        return
    path = generate.write_script(text, output)
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)
    typer.echo(f"Run it with:  python {path}")
    if run:
        core.execute_script(path)


def _emit_relax(cfg: NVTConfig, output: str, to_stdout: bool, run: bool) -> None:
    if not cfg.structure:
        raise typer.BadParameter("a --structure is required.")
    _check_calc(cfg)
    try:
        registry.resolve_optimizer(cfg.job, cfg.optimizer)
    except KeyError as exc:
        raise typer.BadParameter(str(exc))
    text = generate.generate_relax_script(cfg, script_name=output)
    if to_stdout:
        typer.echo(text)
        return
    path = generate.write_script(text, output)
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)
    typer.echo(f"Run it with:  python {path}")
    if run:
        core.execute_script(path)


# --------------------------------------------------------------------------- #
# MD jobs (flag-driven) - one registry-driven command
# --------------------------------------------------------------------------- #
_MD_JOBS = [name for name, spec in registry.JOBS.items()
            if spec.get("category") == "md"]
# Variant choices per calculator, e.g. {'uma': ['oc20', ..., 'omol'], 'mace':
# [...], 'grace': [...]} -- the value of --variant depends on --calculator.
_VARIANTS = {name: list(spec["variants"])
             for name, spec in registry.CALCULATORS.items()
             if spec.get("variants")}
_VARIANT_HELP = "Variant (choices depend on --calculator): " + "; ".join(
    f"{c} -> {vs}" for c, vs in _VARIANTS.items())


@md_app.command("run")
def md_run(
    job: str = typer.Option("nvt", "--job", "-j", help=f"MD job/ensemble: {_MD_JOBS}."),
    checkpoint: str = typer.Option(None, "--checkpoint", "-c",
                                   help="Calculator model/checkpoint file."),
    calculator: str = typer.Option("uma", "--calculator", help=f"MLIP backend: {_CALCULATORS}."),
    variant: Optional[str] = typer.Option(None, "--variant", "-t",
                                          help=_VARIANT_HELP + ". Omit to use the "
                                          "calculator's default."),
    precision: Optional[str] = typer.Option(
        None, "--precision",
        help="Floating-point precision. MACE: float32 | float64 (default "
             "float64). Orb: float32-highest | float32-high | float64 (default "
             "float32-highest). Omit to use the calculator's default."),
    dispersion: str = typer.Option("False", "--dispersion",
                                   help="MACE-MP / Orb-v3 only: add D3 dispersion "
                                        "(True | False)."),
    external_field: Optional[str] = typer.Option(None, "--external-field",
                                                 help="MACE-POLAR only: uniform "
                                                      "field 'Ex Ey Ez'."),
    orcasimpleinput: str = typer.Option(
        "B3LYP def2-SVP", "--orcasimpleinput",
        help="ORCA only: the '!' line (method, basis, ...)."),
    orcablock: Optional[List[str]] = typer.Option(
        None, "--orcablock",
        help="ORCA only: one '% ... end' block; repeat for several (joined with "
             "newlines). The %pal block is added from --nprocs."),
    nprocs: Optional[int] = typer.Option(
        None, "--nprocs",
        help="ORCA only: MPI cores, emitted as '%pal nprocs N end'."),
    orca_command: Optional[str] = typer.Option(
        None, "--orca-command",
        help="ORCA only: full path to the orca binary (OrcaProfile). Omit to use "
             "ASE's configfile / PATH."),
    structure: Optional[str] = typer.Option(None, "--structure", "-s",
                                            help="Starting structure or trajectory."),
    restart: Optional[str] = typer.Option(None, "--restart", "-r",
                                          help="Restart from last frame (positions + velocities)."),
    plumed: Optional[str] = typer.Option(None, "--plumed", "-p",
                                         help="PLUMED input file. If given, the job is biased "
                                              "(metadynamics, walls, ...)."),
    prev_steps: Optional[int] = typer.Option(None, help="Biased restart: previous step count."),
    cell: Optional[str] = typer.Option(None, help='Cell, e.g. "20 20 20".'),
    pbc: str = typer.Option("true", help="Periodicity (true/false or 'T T F')."),
    charge: int = typer.Option(0, help="Total charge."),
    multiplicity: int = typer.Option(1, help="Spin multiplicity (2S+1)."),
    temperature: float = typer.Option(298.15, "--temperature", "-T"),
    timestep: float = typer.Option(0.5, "--timestep", "-dt", help="fs."),
    nsteps: int = typer.Option(10000, "--nsteps", "-n"),
    seed: int = typer.Option(42, "--seed",
                             help="RNG seed for the initial Maxwell-Boltzmann "
                                  "velocities (for reproducibility)."),
    thermostat: Optional[str] = typer.Option(
        None, "--thermostat",
        help=f"NVT thermostat: {_THERMOSTATS} (default nose_hoover). Not used "
             "by NVE."),
    tdamp: Optional[float] = typer.Option(None, "--tdamp",
                                          help="Nose-Hoover coupling time in fs. "
                                               "Omit = auto (100*timestep, min 20 fs)."),
    tchain: int = typer.Option(3, "--tchain", help="Nose-Hoover chain length."),
    tloop: int = typer.Option(1, "--tloop",
                              help="Nose-Hoover inner integration loops."),
    friction: Optional[float] = typer.Option(
        None, "--friction",
        help="Langevin friction coefficient in fs^-1 (default 0.01 = 10 ps^-1). "
             "Typical range 0.001-0.1 fs^-1 (1-100 ps^-1)."),
    taut: Optional[float] = typer.Option(
        None, "--taut",
        help="CSVR (Bussi) coupling time in fs. Omit = auto (100*timestep, "
             "min 20 fs)."),
    traj_interval: int = typer.Option(10, help="Record every N steps."),
    traj_format: str = typer.Option("traj", help="traj | xyz."),
    wrap: bool = typer.Option(False, help="Also wrap the trajectory at the end."),
    output: Optional[str] = typer.Option(None, "--output", "-o",
                                         help="Script filename (default: run_<job>.py)."),
    stdout: bool = typer.Option(False, "--stdout", help="Print the script instead of writing it."),
    run: bool = typer.Option(False, "--run", help="Run the generated script after writing."),
):
    """Generate an MD script. Biasing is an option (--plumed), not a separate job."""
    if job not in _MD_JOBS:
        raise typer.BadParameter(f"unknown job {job!r}; choose from {_MD_JOBS}.")
    cfg = NVTConfig(
        checkpoint=checkpoint, calculator=calculator, variant=variant,
        job=job, precision=precision,
        dispersion=_parse_bool(dispersion, "--dispersion"),
        external_field=external_field,
        orcasimpleinput=orcasimpleinput,
        orcablocks="\n".join(orcablock) if orcablock else None,
        nprocs=nprocs, orca_command=orca_command,
        structure=structure, restart=restart,
        cell=cell, pbc=pbc, charge=charge, multiplicity=multiplicity,
        temperature=temperature, timestep=timestep, nsteps=nsteps, seed=seed,
        thermostat=thermostat,
        tdamp=tdamp, tchain=tchain, tloop=tloop, friction=friction, taut=taut,
        traj_interval=traj_interval, traj_format=traj_format, wrap=wrap,
        plumed=plumed, prev_steps=prev_steps,
    )
    _emit_nvt(cfg, output or f"run_{job}.py", stdout, run)


# --------------------------------------------------------------------------- #
# Single-point calculations (flag-driven)
# --------------------------------------------------------------------------- #
@sp_app.command("run")
def sp_run(
    calculator: str = typer.Option("orca", "--calculator",
                                   help=f"Backend: {_CALCULATORS} (ORCA is the "
                                        "natural single-point calculator)."),
    variant: Optional[str] = typer.Option(None, "--variant", "-t",
                                          help=_VARIANT_HELP + ". Omit to use the "
                                          "calculator's default."),
    checkpoint: str = typer.Option(None, "--checkpoint", "-c",
                                   help="Calculator model/checkpoint file (MLIPs)."),
    precision: Optional[str] = typer.Option(
        None, "--precision", help="MLIP precision (see 'md run --help')."),
    dispersion: str = typer.Option("False", "--dispersion",
                                   help="MACE-MP / Orb-v3 only: add D3 (True|False)."),
    external_field: Optional[str] = typer.Option(None, "--external-field",
                                                 help="MACE-POLAR only: 'Ex Ey Ez'."),
    orcasimpleinput: str = typer.Option(
        "B3LYP def2-SVP", "--orcasimpleinput",
        help="ORCA only: the '!' line (method, basis, ...)."),
    orcablock: Optional[List[str]] = typer.Option(
        None, "--orcablock",
        help="ORCA only: one '% ... end' block; repeat for several."),
    nprocs: Optional[int] = typer.Option(
        None, "--nprocs", help="ORCA only: MPI cores ('%pal nprocs N end')."),
    orca_command: Optional[str] = typer.Option(
        None, "--orca-command", help="ORCA only: full path to the orca binary."),
    structure: str = typer.Option(None, "--structure", "-s",
                                  help="Structure to evaluate."),
    cell: Optional[str] = typer.Option(None, help='Cell, e.g. "20 20 20".'),
    pbc: str = typer.Option("true", help="Periodicity (true/false or 'T T F')."),
    charge: int = typer.Option(0, help="Total charge."),
    multiplicity: int = typer.Option(1, help="Spin multiplicity (2S+1)."),
    device: str = typer.Option("auto", help="Device for MLIP backends."),
    forces: str = typer.Option(
        "True", "--forces",
        help="Compute forces, and stress if periodic (True | False). False is a "
             "cheaper energy-only run."),
    sp_output: str = typer.Option("singlepoint.extxyz", "--sp-output",
                                  help="Results file (extended-xyz)."),
    output: Optional[str] = typer.Option(None, "--output", "-o",
                                         help="Script filename (default: run_sp.py)."),
    stdout: bool = typer.Option(False, "--stdout", help="Print the script instead of writing it."),
    run: bool = typer.Option(False, "--run", help="Run the generated script after writing."),
):
    """Generate a single-point energy/forces script for any calculator."""
    cfg = NVTConfig(
        checkpoint=checkpoint, calculator=calculator, variant=variant,
        job="singlepoint", precision=precision,
        dispersion=_parse_bool(dispersion, "--dispersion"),
        external_field=external_field,
        orcasimpleinput=orcasimpleinput,
        orcablocks="\n".join(orcablock) if orcablock else None,
        nprocs=nprocs, orca_command=orca_command,
        structure=structure, cell=cell, pbc=pbc,
        charge=charge, multiplicity=multiplicity, device=device,
        sp_forces=_parse_bool(forces, "--forces"), sp_output=sp_output,
    )
    _emit_sp(cfg, output or "run_sp.py", stdout, run)


# --------------------------------------------------------------------------- #
# Geometry optimization (flag-driven)
# --------------------------------------------------------------------------- #
@relax_app.command("run")
def relax_run(
    calculator: str = typer.Option("uma", "--calculator",
                                   help=f"Backend: {_CALCULATORS}."),
    variant: Optional[str] = typer.Option(None, "--variant", "-t",
                                          help=_VARIANT_HELP + ". Omit to use the "
                                          "calculator's default."),
    checkpoint: str = typer.Option(None, "--checkpoint", "-c",
                                   help="Calculator model/checkpoint file (MLIPs)."),
    precision: Optional[str] = typer.Option(
        None, "--precision", help="MLIP precision (see 'md run --help')."),
    dispersion: str = typer.Option("False", "--dispersion",
                                   help="MACE-MP / Orb-v3 only: add D3 (True|False)."),
    external_field: Optional[str] = typer.Option(None, "--external-field",
                                                 help="MACE-POLAR only: 'Ex Ey Ez'."),
    orcasimpleinput: str = typer.Option(
        "B3LYP def2-SVP", "--orcasimpleinput",
        help="ORCA only: the '!' line (method, basis, ...)."),
    orcablock: Optional[List[str]] = typer.Option(
        None, "--orcablock",
        help="ORCA only: one '% ... end' block; repeat for several."),
    nprocs: Optional[int] = typer.Option(
        None, "--nprocs", help="ORCA only: MPI cores ('%pal nprocs N end')."),
    orca_command: Optional[str] = typer.Option(
        None, "--orca-command", help="ORCA only: full path to the orca binary."),
    structure: str = typer.Option(None, "--structure", "-s",
                                  help="Structure to relax."),
    cell: Optional[str] = typer.Option(None, help='Cell, e.g. "20 20 20".'),
    pbc: str = typer.Option("true", help="Periodicity (true/false or 'T T F')."),
    charge: int = typer.Option(0, help="Total charge."),
    multiplicity: int = typer.Option(1, help="Spin multiplicity (2S+1)."),
    device: str = typer.Option("auto", help="Device for MLIP backends."),
    optimizer: Optional[str] = typer.Option(
        None, "--optimizer", "-a",
        help=f"Algorithm: {_OPTIMIZERS} (default bfgs)."),
    fmax: float = typer.Option(
        0.05, "--fmax",
        help="Converge until the max force on any atom is below this (eV/A)."),
    nsteps: int = typer.Option(500, "--nsteps", "-n",
                               help="Maximum optimizer steps."),
    output: Optional[str] = typer.Option(None, "--output", "-o",
                                         help="Script filename (default: run_relax.py)."),
    stdout: bool = typer.Option(False, "--stdout", help="Print the script instead of writing it."),
    run: bool = typer.Option(False, "--run", help="Run the generated script after writing."),
):
    """Generate a geometry-optimization script (positions only) for any calculator."""
    cfg = NVTConfig(
        checkpoint=checkpoint, calculator=calculator, variant=variant,
        job="relax", precision=precision,
        dispersion=_parse_bool(dispersion, "--dispersion"),
        external_field=external_field,
        orcasimpleinput=orcasimpleinput,
        orcablocks="\n".join(orcablock) if orcablock else None,
        nprocs=nprocs, orca_command=orca_command,
        structure=structure, cell=cell, pbc=pbc,
        charge=charge, multiplicity=multiplicity, device=device,
        optimizer=optimizer, fmax=fmax, nsteps=nsteps,
        traj="opt", log="opt.log", last_frame="optimized.xyz",
    )
    _emit_relax(cfg, output or "run_relax.py", stdout, run)


# --------------------------------------------------------------------------- #
# Post-processing jobs
# --------------------------------------------------------------------------- #
@post_app.command("wrap")
def postprocess_wrap(
    input: str = typer.Argument(..., help="Trajectory to wrap (.traj, .xyz, ...)."),
    output: str = typer.Option("run_wrap.py", "--output", "-o", help="Script filename."),
    wrapped: Optional[str] = typer.Option(None, "--wrapped", help="Wrapped-trajectory output path."),
    stdout: bool = typer.Option(False, "--stdout", help="Print the script instead of writing it."),
    run: bool = typer.Option(False, "--run", help="Run the generated script after writing."),
):
    """Generate a script that wraps every frame into the primary cell."""
    cfg = WrapConfig(input=input, output=wrapped)
    text = generate.generate_wrap_script(cfg, script_name=output)
    if stdout:
        typer.echo(text)
        return
    path = generate.write_script(text, output)
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)
    typer.echo(f"Run it with:  python {path}")
    if run:
        core.execute_script(path)


# --------------------------------------------------------------------------- #
# Interactive arrow-key wizard
# --------------------------------------------------------------------------- #
def run_wizard() -> None:
    """Interactive menu wizard (implemented in ``cli.wizard``).

    Pick a category and job with the arrow keys, then type the parameters. Back
    and quit are available at every step, and a final review lets any field be
    changed before the reproducible script is written."""
    from . import wizard
    wizard.run()


if __name__ == "__main__":
    app()
