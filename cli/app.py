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

from typing import Optional

import typer

from .config import NVTConfig, WrapConfig
from . import core, generate, registry

app = typer.Typer(
    invoke_without_command=True,   # allow the no-args wizard
    add_completion=True,
    help="Generate (and optionally run) reproducible ASE job scripts.",
)
md_app = typer.Typer(no_args_is_help=True, help="Molecular dynamics jobs.")
post_app = typer.Typer(no_args_is_help=True, help="Post-processing tasks.")
app.add_typer(md_app, name="md")
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


def _emit_nvt(cfg: NVTConfig, output: str, to_stdout: bool, run: bool) -> None:
    if cfg.calculator not in registry.CALCULATORS:
        raise typer.BadParameter(f"unknown calculator {cfg.calculator!r}; "
                                 f"choose from {_CALCULATORS}.")
    if bool(cfg.structure) == bool(cfg.restart):
        raise typer.BadParameter("provide exactly one of --structure or --restart.")

    calc_spec = registry.CALCULATORS[cfg.calculator]
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
        typer.secho("Note: --charge/--multiplicity are only used by UMA's 'omol' "
                    "task, MACE-POLAR and OrbMol-v2; ignoring them here.",
                    fg=typer.colors.YELLOW)
    if cfg.external_field and "EXTERNAL_FIELD" not in comp["params"]:
        typer.secho("Note: --external-field applies only to MACE-POLAR; "
                    "ignoring it here.", fg=typer.colors.YELLOW)

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
