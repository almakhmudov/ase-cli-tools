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


def _emit_nvt(cfg: NVTConfig, output: str, to_stdout: bool, run: bool) -> None:
    if cfg.calculator not in registry.CALCULATORS:
        raise typer.BadParameter(f"unknown calculator {cfg.calculator!r}; "
                                 f"choose from {_CALCULATORS}.")
    if bool(cfg.structure) == bool(cfg.restart):
        raise typer.BadParameter("provide exactly one of --structure or --restart.")
    if not cfg.checkpoint:
        raise typer.BadParameter("a calculator --checkpoint is required.")

    calc_spec = registry.CALCULATORS[cfg.calculator]
    tasks = calc_spec.get("tasks")
    if tasks and cfg.task_name not in tasks:
        raise typer.BadParameter(f"unknown task {cfg.task_name!r} for "
                                 f"{cfg.calculator!r}; choose from {list(tasks)}.")
    # Charge/multiplicity only mean something for the charge/spin task (omol).
    cs_task = calc_spec.get("charge_spin_task")
    if (cs_task and cfg.task_name != cs_task
            and (cfg.charge != 0 or cfg.multiplicity != 1)):
        typer.secho(f"Note: --charge/--multiplicity apply only to the {cs_task!r} "
                    f"task; ignoring them for task {cfg.task_name!r}.",
                    fg=typer.colors.YELLOW)

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
_UMA_TASKS = list(registry.CALCULATORS["uma"].get("tasks", {}))


@md_app.command("run")
def md_run(
    job: str = typer.Option("nvt", "--job", "-j", help=f"MD job/ensemble: {_MD_JOBS}."),
    checkpoint: str = typer.Option(None, "--checkpoint", "-c", help="Calculator checkpoint (.pt)."),
    calculator: str = typer.Option("uma", "--calculator", help=f"MLIP backend: {_CALCULATORS}."),
    task: str = typer.Option("omol", "--task", "-t",
                             help=f"UMA task/property head: {_UMA_TASKS}. Only "
                                  "'omol' uses --charge and --multiplicity."),
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
        checkpoint=checkpoint, calculator=calculator, job=job, task_name=task,
        structure=structure, restart=restart,
        cell=cell, pbc=pbc, charge=charge, multiplicity=multiplicity,
        temperature=temperature, timestep=timestep, nsteps=nsteps,
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
    """Interactive menu: pick a category and job with the arrow keys, then
    type the parameters. Ends by writing a reproducible script."""
    try:
        import questionary
    except ImportError:
        typer.secho("The interactive wizard needs 'questionary' "
                    "(pip install questionary).", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    def _ask(q):
        ans = q.ask()
        if ans is None:                      # user hit Ctrl-C / Esc
            raise typer.Exit()
        return ans

    category = _ask(questionary.select(
        "What would you like to do?",
        choices=[
            questionary.Choice("Molecular dynamics", value="md"),
            questionary.Choice("Post-processing", value="postprocess"),
        ]))

    if category == "postprocess":
        inp = _ask(questionary.path("Trajectory to wrap:"))
        wrapped = _ask(questionary.text("Wrapped-trajectory output (blank = auto):",
                                        default="")) or None
        output = _ask(questionary.text("Script filename:", default="run_wrap.py"))
        cfg = WrapConfig(input=inp, output=wrapped)
        text = generate.generate_wrap_script(cfg, script_name=output)
        path = generate.write_script(text, output)
        typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)
        if _ask(questionary.confirm("Run it now?", default=False)):
            core.execute_script(path)
        return

    # ---- MD ----
    # Job choices are built from the registry (category == "md").
    md_jobs = [questionary.Choice(spec["label"], value=name)
               for name, spec in registry.JOBS.items()
               if spec.get("category") == "md"]
    job = _ask(questionary.select("Which MD job?", choices=md_jobs))

    biased = _ask(questionary.confirm(
        "Add a PLUMED bias (metadynamics, walls, ...)?", default=False))

    # Calculator choices come from the registry too.
    calc_choices = [questionary.Choice(spec["label"], value=name)
                    for name, spec in registry.CALCULATORS.items()]
    calculator = _ask(questionary.select("Calculator:", choices=calc_choices))
    checkpoint = _ask(questionary.path(f"Path to the {calculator} checkpoint (.pt):"))

    # Task / property head (from the registry). Charge & spin are only asked
    # for the calculator's designated charge/spin task (UMA: 'omol').
    calc_spec = registry.CALCULATORS[calculator]
    tasks = calc_spec.get("tasks")
    cs_task = calc_spec.get("charge_spin_task")
    if tasks:
        task_choices = [questionary.Choice(f"{name} - {desc}", value=name)
                        for name, desc in tasks.items()]
        default = cs_task if cs_task in tasks else next(iter(tasks))
        task = _ask(questionary.select("Task (property head):",
                                       choices=task_choices, default=default))
    else:
        task = "omol"

    start_kind = _ask(questionary.select(
        "Start from:",
        choices=[
            questionary.Choice("A structure file", value="structure"),
            questionary.Choice("A restart trajectory (carry velocities)", value="restart"),
        ]))
    start_path = _ask(questionary.path("Path to that file:"))
    structure = start_path if start_kind == "structure" else None
    restart = start_path if start_kind == "restart" else None

    cell = _ask(questionary.text("Cell 'a b c' (blank = use the file's cell):",
                                 default="")) or None
    if cs_task and task == cs_task:
        charge = int(_ask(questionary.text("Charge:", default="0")))
        multiplicity = int(_ask(questionary.text("Spin multiplicity (2S+1):", default="1")))
    else:
        charge, multiplicity = 0, 1
    temperature = float(_ask(questionary.text("Temperature (K):", default="298.15")))
    timestep = float(_ask(questionary.text("Timestep (fs):", default="0.5")))
    nsteps = int(_ask(questionary.text("Number of steps:", default="10000")))
    traj_format = _ask(questionary.select("Trajectory format:", choices=["traj", "xyz"]))
    wrap = _ask(questionary.confirm("Also wrap the trajectory at the end?", default=False))

    plumed = prev_steps = None
    if biased:
        plumed = _ask(questionary.path("PLUMED input file:"))
        if restart:
            ps = _ask(questionary.text("Previous step count (blank = none):", default=""))
            prev_steps = int(ps) if ps else None

    default_name = f"run_{'biased_' if biased else ''}{job}.py"
    output = _ask(questionary.text("Script filename:", default=default_name))

    cfg = NVTConfig(
        checkpoint=checkpoint, calculator=calculator, job=job, task_name=task,
        structure=structure, restart=restart,
        cell=cell, charge=charge, multiplicity=multiplicity,
        temperature=temperature, timestep=timestep, nsteps=nsteps,
        traj_format=traj_format, wrap=wrap, plumed=plumed, prev_steps=prev_steps,
    )
    text = generate.generate_md_script(cfg, script_name=output)
    path = generate.write_script(text, output)
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)
    typer.echo(f"Run it with:  python {path}")
    if _ask(questionary.confirm("Run it now?", default=False)):
        core.execute_script(path)


if __name__ == "__main__":
    app()
