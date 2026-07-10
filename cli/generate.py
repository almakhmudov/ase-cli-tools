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
    nums = [float(x) for x in value.replace(",", " ").split()]
    # 3 (a b c) and 6 (a b c al be ga) are accepted by Cell.new directly; a flat
    # 9-value list is not, so fold it into a 3x3 (row-major) for atoms.set_cell.
    if len(nums) == 9:
        return [nums[0:3], nums[3:6], nums[6:9]]
    return nums


def _parse_vec(value: Optional[str]):
    """Parse a vector like '0 0 0' into a list of floats (or None if blank)."""
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


def _orca_blocks(cfg: NVTConfig) -> str:
    """Assemble ORCA's ``orcablocks`` string: a leading ``%pal nprocs N end``
    when parallelism was requested, then the user's own ``% ... end`` text.
    Several blocks are just newline-separated -- ASE takes one string."""
    blocks = []
    if cfg.nprocs:
        blocks.append(f"%pal nprocs {cfg.nprocs} end")
    if cfg.orcablocks:
        blocks.append(cfg.orcablocks)
    return "\n".join(blocks)


def _orca_simpleinput(orcasimpleinput: str, needs_forces: bool) -> str:
    """ORCA only writes a gradient when the '!' line asks for one, and ASE reads
    forces from that gradient file. Append ``EnGrad`` when forces are needed (any
    MD step; a single point with forces on) and the user has not already
    requested a gradient, so forces 'just work' rather than coming back empty."""
    if not needs_forces or "engrad" in orcasimpleinput.lower():
        return orcasimpleinput
    return f"{orcasimpleinput} EnGrad".strip()


def parse_pseudopotentials(pairs: Optional[List[str]]) -> Optional[Dict[str, str]]:
    """Turn ``['Na=na.UPF', 'Cl=cl.UPF']`` into ``{'Na': 'na.UPF', ...}``.

    Front-end helper (shared by the flags and the wizard); raises ValueError on a
    malformed entry so the caller can report it."""
    if not pairs:
        return None
    out: Dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            raise ValueError(f"pseudopotential {item!r} must be 'Element=file.UPF'")
        element, filename = item.split("=", 1)
        out[element.strip()] = filename.strip()
    return out


def _infer_scalar(value: str):
    """Best-effort type inference for a pw.x keyword value: int, float, bool
    (true/false/.true./.false.) or, failing those, the string as-is."""
    text = value.strip()
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    low = text.lower()
    if low in ("true", ".true."):
        return True
    if low in ("false", ".false."):
        return False
    return text


def parse_input_data(pairs: Optional[List[str]]) -> Optional[Dict[str, object]]:
    """Turn ``['ecutwfc=60', 'disk_io=low']`` into a flat pw.x ``input_data``
    dict with inferred value types. Raises ValueError on a malformed entry."""
    if not pairs:
        return None
    out: Dict[str, object] = {}
    for item in pairs:
        if "=" not in item:
            raise ValueError(f"input entry {item!r} must be 'key=value'")
        key, value = item.split("=", 1)
        out[key.strip()] = _infer_scalar(value)
    return out


def _qe_kpts(value: Optional[str]):
    """Parse a k-point grid '4 4 4' into a tuple, or None for Gamma-point only."""
    if not value:
        return None
    return tuple(int(x) for x in value.replace(",", " ").split())


def _qe_input_data(cfg: NVTConfig, needs_forces: bool) -> Dict[str, object]:
    """Assemble the flat pw.x ``input_data`` dict: the cutoffs, then the user's
    extra keywords, then the force/stress flags when the job needs them (ASE does
    not add these itself, so without them QE prints no forces)."""
    data: Dict[str, object] = {}
    if cfg.ecutwfc is not None:
        data["ecutwfc"] = cfg.ecutwfc
    if cfg.ecutrho is not None:
        data["ecutrho"] = cfg.ecutrho
    if cfg.input_data:
        data.update(cfg.input_data)          # user entries win over the cutoffs
    if needs_forces:
        data.setdefault("tprnfor", True)
        data.setdefault("tstress", True)
    return data


def _apply_force_keywords(values: Dict[str, str], cfg: NVTConfig,
                          needs_forces: bool) -> None:
    """Patch the QM code's force-request keyword into the rendered `values`.

    ASE does not add these automatically: ORCA needs ``EnGrad`` on its "!" line,
    Quantum ESPRESSO needs ``tprnfor``/``tstress`` in ``input_data``. Only done
    when the job actually needs forces (all MD/relax; single-point when on)."""
    if cfg.calculator == "orca":
        values["ORCASIMPLEINPUT"] = repr(
            _orca_simpleinput(cfg.orcasimpleinput, needs_forces))
    elif cfg.calculator == "espresso":
        values["INPUT_DATA"] = repr(_qe_input_data(cfg, needs_forces))


def _charge_spin_block(uses_cs: bool) -> str:
    """The atoms.info charge/spin lines spliced into calculators that read them
    from ``atoms.info`` via the ``$CHARGE_SPIN`` placeholder (UMA). Calculators
    that take charge/spin as constructor args (ORCA) omit the placeholder, so the
    block is silently dropped by ``safe_substitute``."""
    if not uses_cs:
        return ""
    return ("\n# This calculator uses the system's total charge and spin "
            "multiplicity.\n"
            'atoms.info["charge"] = CHARGE\n'
            'atoms.info["spin"] = MULTIPLICITY\n')


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
        # TASK_NAME (UMA) and MODEL (GRACE) are supplied by the chosen variant's
        # fixed "values"; PRECISION is resolved from the variant default.
        "PRECISION": repr(cfg.precision),
        "DISPERSION": str(cfg.dispersion),
        "EXTERNAL_FIELD": repr(_parse_vec(cfg.external_field)),
        "COMMAND": repr(cfg.command),
        "ORCASIMPLEINPUT": repr(cfg.orcasimpleinput),
        "ORCABLOCKS": repr(_orca_blocks(cfg)),
        "PSEUDOPOTENTIALS": repr(cfg.pseudopotentials or {}),
        "PSEUDO_DIR": repr(cfg.pseudo_dir),
        "INPUT_DATA": repr(_qe_input_data(cfg, needs_forces=False)),
        "KPTS": repr(_qe_kpts(cfg.kpts)),
        "SP_FORCES": str(cfg.sp_forces),
        "SP_OUTPUT": repr(cfg.sp_output),
        "TEMPERATURE": str(cfg.temperature),
        "TIMESTEP": str(cfg.timestep),
        "NSTEPS": str(cfg.nsteps),
        "TRAJ_INTERVAL": str(cfg.traj_interval),
        "TDAMP": repr(cfg.tdamp),
        "TCHAIN": str(cfg.tchain),
        "TLOOP": str(cfg.tloop),
        "FRICTION": repr(cfg.friction),
        "TAUT": repr(cfg.taut),
        "SEED": str(cfg.seed),
        "FMAX": str(cfg.fmax),
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

    # MD needs forces at every step; make the QM codes request them.
    _apply_force_keywords(values, cfg, needs_forces=True)

    # Resolve the chosen variant to its component skeleton (every calculator is a
    # variant family). An unknown variant raises KeyError here.
    variant_name, comp = registry.resolve_variant(cfg.calculator, cfg.variant)

    # A variant may pin fixed parameter values baked into the script (a UMA task
    # head, a GRACE model name, ...); these override the cfg-derived values.
    for name, value in comp.get("values", {}).items():
        values[name] = repr(value)

    # Precision: fall back to the variant's default when the config leaves it
    # unset (None). Maps to MACE's default_dtype / Orb's precision in templates.
    pspec = comp.get("precision")
    if pspec:
        values["PRECISION"] = repr(cfg.precision or pspec["default"])

    # Charge/spin. A variant that uses them may either bake the atoms.info lines
    # into its own skeleton (MACE-POLAR, OrbMol-v2) or rely on the $CHARGE_SPIN
    # placeholder (UMA). We always compute the block and hand it to
    # safe_substitute; skeletons without the placeholder simply ignore it.
    uses_cs = bool(comp.get("uses_charge_spin"))
    charge_spin_block = _charge_spin_block(uses_cs)

    # Resolve the dynamics driver: a thermostat for a thermostatted job (NVT), or
    # a fixed integrator (NVE). The driver skeleton builds the ASE ``dyn`` object
    # and is spliced into the shared MD tail at its $DRIVER slot.
    thermo_name, thermo = registry.resolve_thermostat(cfg.job, cfg.thermostat)
    if thermo is not None:
        driver_template = thermo["template"]
        driver_params = thermo["params"]
    else:
        driver_template = job["driver_template"]
        driver_params = []

    # Collect the parameter names contributed by each selected component.
    keys = list(registry.SHARED_PARAMS)
    keys += comp["params"]
    if uses_cs and "CHARGE" not in comp["params"]:
        keys += ["CHARGE", "MULTIPLICITY"]
    keys += job["params"]
    keys += driver_params
    if cfg.wrap:
        keys += registry.FEATURES["wrap"]["params"]
    if biased:
        keys += registry.FEATURES["plumed"]["params"]

    plumed_lines = _read_plumed_lines(cfg.plumed) if biased else None
    params_block = _render_params(keys, values, plumed_lines)

    job_label = job["label"]
    if thermo is not None:
        job_label += f" ({thermo['label']})"
    title = f"{job_label} with {comp.get('label', calc['label'])}"
    if biased:
        title += " + PLUMED"

    # Assemble: header + preamble + calculator + attach + job (+ wrap feature).
    parts = [
        _header(title, script_name),
        Template(_load("preamble.py.tmpl")).substitute(PARAMS=params_block),
        Template(_load(comp["template"])).safe_substitute(
            CHARGE_SPIN=charge_spin_block),
        _load(registry.FEATURES["plumed"]["attach_template"]) if biased
        else _load("attach/plain.py.tmpl"),
    ]
    writer = _load("writers/traj.py.tmpl" if fmt == "traj" else "writers/xyz.py.tmpl")
    driver_text = _load(driver_template).strip("\n")
    parts.append(
        Template(_load(job["template"])).substitute(
            DRIVER=driver_text, TRAJ_WRITER=writer.rstrip("\n"))
    )
    if cfg.wrap:
        parts.append(_load(registry.FEATURES["wrap"]["append_template"]))

    return "".join(parts)


# Backwards-compatible alias.
generate_nvt_script = generate_md_script


def generate_singlepoint_script(cfg: NVTConfig,
                                script_name: str = "run_sp.py") -> str:
    """Assemble a single-point (energy/forces) script.

    Shares the header + preamble + calculator + attach with the MD path, but the
    job tail just evaluates the calculator and writes the results -- no
    velocities, thermostat or trajectory. Works with any calculator (the ORCA
    QM code is its natural pairing, but an MLIP single point is valid too)."""
    if cfg.calculator not in registry.CALCULATORS:
        raise KeyError(f"unknown calculator {cfg.calculator!r}; "
                       f"available: {sorted(registry.CALCULATORS)}")
    if cfg.job not in registry.JOBS:
        raise KeyError(f"unknown job {cfg.job!r}; "
                       f"available: {sorted(registry.JOBS)}")

    calc = registry.CALCULATORS[cfg.calculator]
    job = registry.JOBS[cfg.job]
    traj_path, wrapped_path, _ = _traj_paths(cfg)
    values = _param_values(cfg, traj_path, wrapped_path)

    # Request forces from the QM codes only when actually wanted, so an
    # energy-only single point stays cheap.
    _apply_force_keywords(values, cfg, needs_forces=cfg.sp_forces)

    variant_name, comp = registry.resolve_variant(cfg.calculator, cfg.variant)
    for name, value in comp.get("values", {}).items():
        values[name] = repr(value)
    pspec = comp.get("precision")
    if pspec:
        values["PRECISION"] = repr(cfg.precision or pspec["default"])

    uses_cs = bool(comp.get("uses_charge_spin"))
    charge_spin_block = _charge_spin_block(uses_cs)

    keys = list(registry.SHARED_PARAMS)
    keys += comp["params"]
    if uses_cs and "CHARGE" not in comp["params"]:
        keys += ["CHARGE", "MULTIPLICITY"]
    keys += job["params"]
    params_block = _render_params(keys, values, None)

    title = f"{job['label']} with {comp.get('label', calc['label'])}"

    parts = [
        _header(title, script_name),
        Template(_load("preamble.py.tmpl")).substitute(PARAMS=params_block),
        Template(_load(comp["template"])).safe_substitute(
            CHARGE_SPIN=charge_spin_block),
        _load("attach/plain.py.tmpl"),
        _load(job["template"]),
    ]
    return "".join(parts)


def generate_relax_script(cfg: NVTConfig,
                          script_name: str = "run_relax.py") -> str:
    """Assemble a geometry-optimization script.

    Like single-point it shares the header/preamble/calculator/attach, then
    splices the chosen ASE optimizer (BFGS/LBFGS/FIRE) into the relax tail and
    runs to the force threshold. Forces are needed every step, so ORCA is made
    to request a gradient."""
    if cfg.calculator not in registry.CALCULATORS:
        raise KeyError(f"unknown calculator {cfg.calculator!r}; "
                       f"available: {sorted(registry.CALCULATORS)}")
    if cfg.job not in registry.JOBS:
        raise KeyError(f"unknown job {cfg.job!r}; "
                       f"available: {sorted(registry.JOBS)}")

    calc = registry.CALCULATORS[cfg.calculator]
    job = registry.JOBS[cfg.job]
    traj_path, wrapped_path, _ = _traj_paths(cfg)
    values = _param_values(cfg, traj_path, wrapped_path)

    # A relaxation evaluates forces at every step; make the QM codes request them.
    _apply_force_keywords(values, cfg, needs_forces=True)

    variant_name, comp = registry.resolve_variant(cfg.calculator, cfg.variant)
    for name, value in comp.get("values", {}).items():
        values[name] = repr(value)
    pspec = comp.get("precision")
    if pspec:
        values["PRECISION"] = repr(cfg.precision or pspec["default"])

    uses_cs = bool(comp.get("uses_charge_spin"))
    charge_spin_block = _charge_spin_block(uses_cs)

    # Resolve the optimizer algorithm (the relax counterpart of a thermostat).
    opt_name, opt = registry.resolve_optimizer(cfg.job, cfg.optimizer)

    keys = list(registry.SHARED_PARAMS)
    keys += comp["params"]
    if uses_cs and "CHARGE" not in comp["params"]:
        keys += ["CHARGE", "MULTIPLICITY"]
    keys += job["params"]
    params_block = _render_params(keys, values, None)

    title = (f"{job['label']} ({opt['label']}) with "
             f"{comp.get('label', calc['label'])}")

    parts = [
        _header(title, script_name),
        Template(_load("preamble.py.tmpl")).substitute(PARAMS=params_block),
        Template(_load(comp["template"])).safe_substitute(
            CHARGE_SPIN=charge_spin_block),
        _load("attach/plain.py.tmpl"),
        Template(_load(job["template"])).substitute(OPTIMIZER=opt["class"]),
    ]
    return "".join(parts)


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
