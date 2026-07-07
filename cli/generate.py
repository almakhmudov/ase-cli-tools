"""Assemble standalone, runnable ASE scripts from skeleton templates.

The generator is deliberately dumb: it stitches together skeleton files
(``templates/``) chosen via the component ``registry`` and bakes the parameter
values into a single block. All domain code lives in the templates, so adding a
calculator or job is a matter of a new template + a registry entry -- the ASE
way of composing swappable pieces.
"""

from __future__ import annotations

import os
import stat
from datetime import date
from string import Template
from typing import Dict, List, Optional

from . import __version__, registry
from .config import NVTConfig, WrapConfig

_TDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


# --------------------------------------------------------------------------- #
# template loading + small parsing helpers
# --------------------------------------------------------------------------- #
def _load(rel: str) -> str:
    with open(os.path.join(_TDIR, rel)) as fh:
        return fh.read()


_TRUE = {"true", "t", "1", "yes", "y", "on"}


def _parse_pbc(value: str):
    vals = [tok.lower() in _TRUE for tok in value.replace(",", " ").split()]
    return vals[0] if len(vals) == 1 else vals


def _parse_cell(value: Optional[str]):
    if not value:
        return None
    return [float(x) for x in value.replace(",", " ").split()]


def _read_plumed_lines(path: str) -> List[str]:
    """Read a PLUMED input, stripping comments and blank lines."""
    lines = []
    with open(path) as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].rstrip()
            if line.strip():
                lines.append(line)
    return lines


def _traj_paths(cfg: NVTConfig):
    ext = "traj" if cfg.traj_format == "traj" else "xyz"
    stem = os.path.splitext(cfg.traj)[0]
    return f"{stem}.{ext}", f"{stem}_wrapped.{ext}", cfg.traj_format


# --------------------------------------------------------------------------- #
# parameter rendering
# --------------------------------------------------------------------------- #
def _param_values(cfg: NVTConfig, traj_path: str, wrapped_path: str) -> Dict[str, str]:
    """Map every possible parameter NAME to its Python-literal text."""
    return {
        "STRUCTURE": repr(cfg.structure),
        "RESTART": repr(cfg.restart),
        "DEVICE": repr(cfg.device),
        "CELL": repr(_parse_cell(cfg.cell)),
        "PBC": repr(_parse_pbc(cfg.pbc)),
        "CHARGE": str(cfg.charge),
        "MULTIPLICITY": str(cfg.multiplicity),
        "CHECKPOINT": repr(cfg.checkpoint),
        "TASK_NAME": repr(cfg.task_name),
        "TEMPERATURE": str(cfg.temperature),
        "TIMESTEP": str(cfg.timestep),
        "NSTEPS": str(cfg.nsteps),
        "TRAJ_INTERVAL": str(cfg.traj_interval),
        "TDAMP": repr(cfg.tdamp),
        "TCHAIN": str(cfg.tchain),
        "TLOOP": str(cfg.tloop),
        "SEED": str(cfg.seed),
        "TRAJ": repr(traj_path),
        "LOG": repr(cfg.log),
        "LAST_FRAME": repr(cfg.last_frame),
        "WRAPPED": repr(wrapped_path),
        "PLUMED_LOG": repr(cfg.plumed_log),
        "PREV_STEPS": repr(cfg.prev_steps),
    }


def _render_params(keys: List[str], values: Dict[str, str],
                   plumed_lines: Optional[List[str]]) -> str:
    width = max((len(k) for k in keys), default=0)
    lines = [f"{k:<{width}} = {values[k]}" for k in keys]
    if plumed_lines is not None:
        lines.append("")
        lines.append("# PLUMED input embedded for reproducibility "
                     "(one command per line):")
        lines.append("PLUMED_INPUT = [")
        lines += [f"    {line!r}," for line in plumed_lines]
        lines.append("]")
    return "\n".join(lines)


def _header(title: str, script_name: str) -> str:
    return Template(_load("header.py.tmpl")).substitute(
        TITLE=title, VERSION=__version__, DATE=date.today(),
        SCRIPT_NAME=script_name)


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def generate_md_script(cfg: NVTConfig, script_name: str = "run_md.py") -> str:
    """Assemble an MD script from the registry components named in `cfg`.

    Which calculator and job skeleton are used is looked up by name, so this
    function does not need to change when new components are registered.
    """
    if cfg.calculator not in registry.CALCULATORS:
        raise KeyError(f"unknown calculator {cfg.calculator!r}; "
                       f"available: {sorted(registry.CALCULATORS)}")
    if cfg.job not in registry.JOBS:
        raise KeyError(f"unknown job {cfg.job!r}; "
                       f"available: {sorted(registry.JOBS)}")

    calc = registry.CALCULATORS[cfg.calculator]
    job = registry.JOBS[cfg.job]
    biased = cfg.plumed is not None
    traj_path, wrapped_path, fmt = _traj_paths(cfg)
    values = _param_values(cfg, traj_path, wrapped_path)

    # Validate the calculator task, if the calculator declares a task set.
    tasks = calc.get("tasks")
    if tasks and cfg.task_name not in tasks:
        raise KeyError(f"unknown task {cfg.task_name!r} for calculator "
                       f"{cfg.calculator!r}; available: {sorted(tasks)}")

    # The charge/spin params (and the lines that set them) apply only to the
    # calculator's designated charge/spin task (UMA: 'omol').
    cs_task = calc.get("charge_spin_task")
    use_charge_spin = cs_task is not None and cfg.task_name == cs_task
    charge_spin_block = (
        f"\n# The {cs_task!r} task uses the system's total charge and spin "
        f"multiplicity.\n"
        'atoms.info["charge"] = CHARGE\n'
        'atoms.info["spin"] = MULTIPLICITY\n'
        if use_charge_spin else ""
    )

    # Collect the parameter names contributed by each selected component.
    keys = list(registry.SHARED_PARAMS)
    keys += calc["params"]
    if use_charge_spin:
        keys += ["CHARGE", "MULTIPLICITY"]
    keys += job["params"]
    if cfg.wrap:
        keys += registry.FEATURES["wrap"]["params"]
    if biased:
        keys += registry.FEATURES["plumed"]["params"]

    plumed_lines = _read_plumed_lines(cfg.plumed) if biased else None
    params_block = _render_params(keys, values, plumed_lines)

    title = f"{job['label']} with {calc['label']} ({cfg.task_name})"
    if biased:
        title += " + PLUMED"

    # Assemble: header + preamble + calculator + attach + job (+ wrap feature).
    parts = [
        _header(title, script_name),
        Template(_load("preamble.py.tmpl")).substitute(PARAMS=params_block),
        Template(_load(calc["template"])).safe_substitute(
            CHARGE_SPIN=charge_spin_block),
        _load(registry.FEATURES["plumed"]["attach_template"]) if biased
        else _load("attach/plain.py.tmpl"),
    ]
    writer = _load("writers/traj.py.tmpl" if fmt == "traj" else "writers/xyz.py.tmpl")
    parts.append(
        Template(_load(job["template"])).substitute(TRAJ_WRITER=writer.rstrip("\n"))
    )
    if cfg.wrap:
        parts.append(_load(registry.FEATURES["wrap"]["append_template"]))

    return "".join(parts)


# Backwards-compatible alias.
generate_nvt_script = generate_md_script


def generate_wrap_script(cfg: WrapConfig, script_name: str = "run_wrap.py") -> str:
    """Assemble a standalone trajectory-wrapping script."""
    output = cfg.output or (os.path.splitext(cfg.input)[0] + "_wrapped.extxyz")
    header = _header("Wrap trajectory frames into the cell", script_name)
    body = Template(_load(registry.POSTPROCESS["wrap"]["template"])).substitute(
        INPUT=repr(cfg.input), OUTPUT=repr(output))
    return header + "\n" + body


def write_script(text: str, path: str) -> str:
    """Write a generated script to `path` and make it executable."""
    with open(path, "w") as fh:
        fh.write(text)
    mode = os.stat(path).st_mode
    os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path
