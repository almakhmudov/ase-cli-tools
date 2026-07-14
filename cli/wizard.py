"""Interactive arrow-key wizard with full navigation.

The wizard is a list of ordered ``Step`` descriptors driven by a small engine.
Compared with a straight run of prompts this buys three things the user can do
at any time:

* **quit** the whole process (every menu has a quit entry; typed prompts accept
  ``!q``; Esc/Ctrl-C also quit);
* **go back** to the previous answer -- including the job type -- (menus have a
  back entry; typed prompts accept ``<``);
* at the end, a **review** screen where any part of the job can be changed
  *except the job type* (the category and the ensemble), because those decide
  which questions exist in the first place.

Each step knows when it ``applies`` (so charge/spin, MACE variants, PLUMED
options, ... appear only when relevant), how to ask itself, and -- for the
handful of "structural" choices (calculator, variant, task, ...) -- which
downstream answers it invalidates when changed. Editing a structural field in
the review re-asks exactly the dependent fields that changed relevance.

The engine collects a plain ``state`` dict; the generated script is assembled
from it through the same ``config``/``generate`` path the flags use.
"""

from __future__ import annotations

from typing import List

import typer

from . import core, generate, registry
from .config import NVTConfig, WrapConfig


# --------------------------------------------------------------------------- #
# navigation sentinels
# --------------------------------------------------------------------------- #
class _Sentinel:
    def __init__(self, name: str):
        self._name = name

    def __repr__(self):
        return self._name


BACK = _Sentinel("BACK")
EXIT = _Sentinel("EXIT")
_MISSING = _Sentinel("MISSING")

_TIP = ("Navigation: on a menu pick '↩ back' or '✕ quit'; at a typed prompt "
        "enter '<' to go back or '!q' to quit. A blank keeps the default in ().")


# --------------------------------------------------------------------------- #
# low-level prompts (all understand back / quit)
# --------------------------------------------------------------------------- #
def _fmt_default(value) -> str:
    if value is None:
        return "none"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value)


def ask_select(q, message, pairs, default_val=None, allow_back=True):
    """Menu prompt. `pairs` is a list of (title, value). Returns a value, or the
    BACK / EXIT sentinel."""
    choices = [q.Choice(title, value) for title, value in pairs]
    if allow_back:
        choices.append(q.Choice("↩  go back", BACK))
    choices.append(q.Choice("✕  quit", EXIT))
    kwargs = {}
    if any(v == default_val for _, v in pairs):
        kwargs["default"] = default_val
    ans = q.select(f"{message}:", choices=choices, **kwargs).ask()
    return EXIT if ans is None else ans


def ask_bool(q, message, default_bool, allow_back=True):
    return ask_select(q, message, [("Yes", True), ("No", False)],
                      default_val=bool(default_bool), allow_back=allow_back)


def ask_text(q, message, default, cast=str, allow_back=True):
    """Free-text prompt. Shows the default in parentheses (unless the message
    already mentions one), treats a blank as that default, and understands the
    '<' (back) and '!q' (quit) tokens. Re-asks on a bad cast."""
    if "default" in message.lower():
        prompt = f"{message}:"
    else:
        prompt = f"{message} (default {_fmt_default(default)}):"
    while True:
        ans = q.text(prompt).ask()
        if ans is None:
            return EXIT
        ans = ans.strip()
        if allow_back and ans == "<":
            return BACK
        if ans in ("!q", "!quit"):
            return EXIT
        if not ans:
            return default
        try:
            return cast(ans)
        except (ValueError, TypeError):
            typer.secho("  invalid value, please try again.", fg=typer.colors.RED)


def ask_path(q, message, allow_back=True):
    """Filesystem-path prompt (required). Understands '<' (back) and '!q'."""
    while True:
        ans = q.path(f"{message}:").ask()
        if ans is None:
            return EXIT
        ans = ans.strip()
        if allow_back and ans == "<":
            return BACK
        if ans in ("!q", "!quit"):
            return EXIT
        if ans:
            return ans
        typer.secho("  a path is required.", fg=typer.colors.RED)


# --------------------------------------------------------------------------- #
# registry-aware predicates
# --------------------------------------------------------------------------- #
def _is_md(s):
    return s.get("category") == "md"


def _is_sp(s):
    return s.get("category") == "singlepoint"


def _is_relax(s):
    return s.get("category") == "relax"


def _is_pp(s):
    return s.get("category") == "postprocess"


def _wants_calc(s):
    """A calculator is chosen for MD, single-point and relax jobs."""
    return _is_md(s) or _is_sp(s) or _is_relax(s)


def _is_orca(s):
    return _wants_calc(s) and s.get("calculator") == "orca"


def _is_espresso(s):
    return _wants_calc(s) and s.get("calculator") == "espresso"


def _needs_command(s):
    """The executable prompt applies to the external codes (ORCA, QE)."""
    return _is_orca(s) or _is_espresso(s)


def _comp_params(s):
    calc = s.get("calculator")
    if not calc:
        return []
    _, comp = registry.resolve_variant(calc, s.get("variant"))
    return comp["params"]


def _has_variants(s):
    calc = s.get("calculator")
    return bool(calc and registry.CALCULATORS[calc].get("variants"))


def _uses_cs(s):
    calc = s.get("calculator")
    return bool(calc and registry.uses_charge_spin(calc, s.get("variant")))


def _has_thermostat(s):
    job = s.get("job")
    return bool(_is_md(s) and job
                and registry.JOBS.get(job, {}).get("thermostats"))


def _thermostat_is(s, name):
    """Whether the job is thermostatted and the chosen thermostat is `name`
    (falling back to the job's default before the step is answered)."""
    if not _has_thermostat(s):
        return False
    default = registry.JOBS[s["job"]].get("default_thermostat")
    return s.get("thermostat", default) == name


# --------------------------------------------------------------------------- #
# choice builders (depend on earlier answers)
# --------------------------------------------------------------------------- #
def _calc_pairs(_s):
    # Show the bare calculator name in the menu, dropping the parenthetical tag
    # ("UMA (FairChem)" -> "UMA", "Quantum ESPRESSO (QM)" -> "Quantum ESPRESSO").
    return [(spec["label"].split(" (")[0], name)
            for name, spec in registry.CALCULATORS.items()]


def _variant_pairs(s):
    variants = registry.CALCULATORS[s["calculator"]]["variants"]
    return [(f"{v['label']} - {v['desc']}", name) for name, v in variants.items()]


def _variant_default(s):
    return registry.CALCULATORS[s["calculator"]].get("default_variant")


def _md_job_pairs(_s):
    return [(spec["label"], name) for name, spec in registry.JOBS.items()
            if spec.get("category") == "md"]


def _thermostat_pairs(_s):
    return [(spec["label"], name)
            for name, spec in registry.THERMOSTATS.items()]


def _thermostat_default(s):
    return registry.JOBS[s["job"]].get("default_thermostat")


def _precision_spec(s):
    return (registry.precision_spec(s["calculator"], s.get("variant"))
            or {"default": None, "choices": []})


def _precision_default(s):
    return _precision_spec(s)["default"]


def _precision_pairs(s):
    spec = _precision_spec(s)
    return [(f"{c} (recommended)" if c == spec["default"] else c, c)
            for c in spec["choices"]]


def _optimizer_pairs(_s):
    return [(spec["label"], name)
            for name, spec in registry.OPTIMIZERS.items()]


def _optimizer_default(s):
    return registry.JOBS["relax"].get("default_optimizer")


def _pseudos_cast(value):
    """Wizard entry 'Na=na.UPF Cl=cl.UPF' -> {'Na': 'na.UPF', 'Cl': 'cl.UPF'}."""
    return generate.parse_pseudopotentials(value.split()) or {}


def _input_data_cast(value):
    """Wizard entry 'ecutwfc=60 disk_io=low' -> a flat pw.x input_data dict."""
    return generate.parse_input_data(value.split()) or {}


def _fmt_dict(value):
    """Compact preview of a dict (pseudopotentials / input_data) for the review."""
    if not value:
        return "none"
    return ", ".join(f"{k}={v}" for k, v in value.items())


def _orcablocks_cast(value):
    r"""Turn a typed literal ``\n`` into a real newline so several ORCA blocks can
    be entered on one prompt line (the CLI uses a repeatable flag instead)."""
    return value.replace(r"\n", "\n")


def _fmt_blocks(value):
    """Compact one-line preview of the ORCA %-blocks for the review screen."""
    if not value:
        return "none"
    return " / ".join(value.splitlines())


def _default_output(s):
    cat = s.get("category")
    if cat == "postprocess":
        return "run_wrap.py"
    if cat == "singlepoint":
        return "run_sp.py"
    if cat == "relax":
        return "run_relax.py"
    return f"run_{'biased_' if s.get('biased') else ''}{s.get('job', 'nvt')}.py"


# --------------------------------------------------------------------------- #
# step descriptor
# --------------------------------------------------------------------------- #
class Step:
    def __init__(self, key, kind, message, *, applies=lambda s: True,
                 choices=None, default=None, cast=str, editable=True,
                 label=None, clears=(), fmt=None):
        self.key = key
        self.kind = kind                 # 'select' | 'bool' | 'text' | 'path'
        self._message = message          # str or callable(state) -> str
        self.applies = applies
        self._choices = choices          # list[(title,value)] or callable(state)
        self._default = default          # value or callable(state)
        self.cast = cast
        self.editable = editable
        self.label = label or key
        self.clears = tuple(clears)
        self._fmt = fmt

    def message(self, state):
        return self._message(state) if callable(self._message) else self._message

    def default_value(self, state):
        return self._default(state) if callable(self._default) else self._default

    def choices(self, state):
        return self._choices(state) if callable(self._choices) else self._choices

    def fmt_value(self, state):
        value = state.get(self.key, self.default_value(state))
        if self._fmt:
            return self._fmt(value)
        return _fmt_default(value)

    def ask(self, q, state, current, allow_back=True):
        msg = self.message(state)
        if self.kind == "select":
            return ask_select(q, msg, self.choices(state), default_val=current,
                              allow_back=allow_back)
        if self.kind == "bool":
            return ask_bool(q, msg, current, allow_back=allow_back)
        if self.kind == "path":
            return ask_path(q, msg, allow_back=allow_back)
        return ask_text(q, msg, current, cast=self.cast, allow_back=allow_back)


# --------------------------------------------------------------------------- #
# the ordered step list
# --------------------------------------------------------------------------- #
def _build_steps() -> List[Step]:
    return [
        # --- job type (NOT editable in the review) --------------------------
        Step("category", "select", "What would you like to do?",
             choices=[("Molecular dynamics", "md"),
                      ("Single-point energy & forces", "singlepoint"),
                      ("Geometry optimization", "relax"),
                      ("Post-processing (wrap a trajectory)", "postprocess")],
             default="md", editable=False, label="Job category"),

        # --- post-processing branch -----------------------------------------
        Step("pp_input", "path", "Trajectory to wrap",
             applies=_is_pp, label="Trajectory to wrap"),
        Step("pp_wrapped", "text", "Wrapped-trajectory output (default: auto)",
             applies=_is_pp, default=None, label="Wrapped output"),

        # --- MD: job type (NOT editable in the review) ----------------------
        Step("job", "select", "Which MD job (ensemble)?",
             applies=_is_md, choices=_md_job_pairs, default="nvt",
             editable=False, label="MD ensemble"),

        # --- MD: calculator + variant ---------------------------------------
        Step("biased", "bool",
             "Add a PLUMED bias (metadynamics, walls, ...)?",
             applies=_is_md, default=False, label="PLUMED bias",
             clears=("plumed", "prev_steps")),
        Step("calculator", "select", "Calculator?",
             applies=_wants_calc, choices=_calc_pairs, default="uma",
             label="Calculator",
             clears=("variant", "checkpoint", "precision", "dispersion",
                     "charge", "multiplicity", "external_field",
                     "orcasimpleinput", "nprocs", "orcablocks", "command",
                     "pseudopotentials", "pseudo_dir", "ecutwfc", "ecutrho",
                     "kpts", "input_data")),
        Step("variant", "select",
             lambda s: f"{registry.CALCULATORS[s['calculator']]['label']} "
                       "variant / model / task?",
             applies=lambda s: _wants_calc(s) and _has_variants(s),
             choices=_variant_pairs, default=_variant_default,
             label="Variant",
             clears=("checkpoint", "precision", "dispersion", "charge",
                     "multiplicity", "external_field")),
        Step("checkpoint", "path",
             lambda s: f"Path to the {s.get('variant') or s['calculator']} "
                       f"model/checkpoint file",
             applies=lambda s: _wants_calc(s) and "CHECKPOINT" in _comp_params(s),
             label="Model/checkpoint"),
        Step("precision", "select", "Model precision?",
             applies=lambda s: _wants_calc(s) and "PRECISION" in _comp_params(s),
             choices=_precision_pairs, default=_precision_default,
             label="Precision"),
        Step("dispersion", "bool", "Add a D3 dispersion correction?",
             applies=lambda s: _wants_calc(s) and "DISPERSION" in _comp_params(s),
             default=False, label="D3 dispersion"),

        # --- ORCA (QM): free-text input, %-blocks and the binary path -------
        Step("orcasimpleinput", "text",
             "ORCA '!' line (method, basis, ...)",
             applies=_is_orca, default="B3LYP def2-SVP",
             label="ORCA simple input"),
        Step("nprocs", "text",
             "MPI cores (adds '%pal nprocs N end'; blank = serial)",
             applies=_is_orca, default=None, cast=int, label="MPI cores (nprocs)"),
        Step("orcablocks", "text",
             r"Extra '% ... end' blocks (use \n between several; blank = none)",
             applies=_is_orca, default=None, cast=_orcablocks_cast,
             label="ORCA %-blocks", fmt=_fmt_blocks),

        # --- Quantum ESPRESSO (QM) ------------------------------------------
        Step("pseudopotentials", "text",
             "Pseudopotentials 'El=file.UPF' (space-separated; required)",
             applies=_is_espresso, default={}, cast=_pseudos_cast,
             label="Pseudopotentials", fmt=_fmt_dict),
        Step("pseudo_dir", "text",
             "Pseudopotential directory (blank = ASE config)",
             applies=_is_espresso, default=None, label="Pseudo dir"),
        Step("ecutwfc", "text",
             "Plane-wave cutoff ecutwfc (Ry)",
             applies=_is_espresso, default=60, cast=float, label="ecutwfc (Ry)"),
        Step("ecutrho", "text",
             "Charge-density cutoff ecutrho (Ry)",
             applies=_is_espresso, default=480, cast=float, label="ecutrho (Ry)"),
        Step("kpts", "text",
             "k-point grid 'k1 k2 k3' (blank = Gamma only)",
             applies=_is_espresso, default=None, label="k-points"),
        Step("input_data", "text",
             "Extra pw.x keywords 'key=value' (space-separated; blank = none)",
             applies=_is_espresso, default={}, cast=_input_data_cast,
             label="Extra pw.x keywords", fmt=_fmt_dict),

        # --- external codes: the executable ---------------------------------
        Step("command", "text",
             "Executable (e.g. /path/to/pw.x or 'mpiexec -n 16 .../pw.x'; "
             "blank = ASE config / PATH)",
             applies=_needs_command, default=None, label="Executable command"),

        # --- structure ------------------------------------------------------
        Step("start_kind", "select", "Start from?",
             applies=_is_md,
             choices=[("A structure file", "structure"),
                      ("A restart trajectory (carry velocities)", "restart")],
             default="structure", label="Start from",
             clears=("start_path", "prev_steps"),
             fmt=lambda v: {"structure": "structure file",
                            "restart": "restart trajectory"}.get(v, str(v))),
        Step("start_path", "path",
             lambda s: ("Path to the restart trajectory"
                        if s.get("start_kind") == "restart"
                        else "Path to the structure file"),
             applies=_wants_calc, label="Start file"),
        Step("cell", "text", "Cell 'a b c' (default: use the file's cell)",
             applies=_wants_calc, default=None, label="Cell"),
        Step("pbc", "text", "Periodicity (true/false, or 'T T F' per axis)",
             applies=_wants_calc, default="true", label="Periodicity (pbc)"),

        # --- charge / spin / field (only when the calc/task uses them) ------
        Step("charge", "text", "Charge",
             applies=lambda s: _wants_calc(s) and _uses_cs(s), default=0, cast=int,
             label="Charge"),
        Step("multiplicity", "text", "Spin multiplicity (2S+1)",
             applies=lambda s: _wants_calc(s) and _uses_cs(s), default=1, cast=int,
             label="Spin multiplicity"),
        Step("external_field", "text",
             "External field 'Ex Ey Ez' (default: none)",
             applies=lambda s: (_wants_calc(s) and _uses_cs(s)
                                and "EXTERNAL_FIELD" in _comp_params(s)),
             default=None, label="External field"),

        # --- MD: dynamics ---------------------------------------------------
        Step("temperature", "text",
             lambda s: ("Initial temperature (K) — sets the starting velocities"
                        if s.get("job") == "nve" else "Temperature (K)"),
             applies=_is_md, default=298.15, cast=float, label="Temperature (K)"),
        Step("timestep", "text", "Timestep (fs)",
             applies=_is_md, default=0.5, cast=float, label="Timestep (fs)"),
        Step("nsteps", "text",
             lambda s: ("Maximum optimizer steps" if _is_relax(s)
                        else "Number of steps"),
             applies=lambda s: _is_md(s) or _is_relax(s),
             default=lambda s: 500 if _is_relax(s) else 10000, cast=int,
             label="Number of steps"),
        Step("seed", "text", "RNG seed for the initial velocities",
             applies=_is_md, default=42, cast=int, label="RNG seed"),

        # --- relax: optimizer + convergence ---------------------------------
        Step("optimizer", "select", "Optimizer algorithm?",
             applies=_is_relax, choices=_optimizer_pairs,
             default=_optimizer_default, label="Optimizer"),
        Step("fmax", "text", "Force convergence threshold fmax (eV/A)",
             applies=_is_relax, default=0.05, cast=float, label="fmax (eV/A)"),

        # --- MD: thermostat (thermostatted jobs only, e.g. NVT) -------------
        Step("thermostat", "select", "Thermostat?",
             applies=_has_thermostat, choices=_thermostat_pairs,
             default=_thermostat_default, label="Thermostat",
             clears=("friction", "taut", "adjust_thermostat",
                     "tdamp", "tchain", "tloop")),
        # Langevin: friction, in fs^-1 (converted to ASE units in the template).
        Step("friction", "text",
             "Langevin friction (fs^-1)",
             applies=lambda s: _thermostat_is(s, "langevin"),
             default=0.01, cast=float, label="Friction (fs^-1)"),
        # CSVR (Bussi): coupling time.
        Step("taut", "text",
             "Coupling time taut (fs) (default: auto = 100*timestep, min 20)",
             applies=lambda s: _thermostat_is(s, "csvr"),
             default=None, cast=float, label="taut (fs)"),
        # Nose-Hoover: chain parameters, behind an opt-in.
        Step("adjust_thermostat", "bool",
             "Adjust Nose-Hoover thermostat parameters?",
             applies=lambda s: _thermostat_is(s, "nose_hoover"),
             default=False, label="Custom thermostat",
             clears=("tdamp", "tchain", "tloop")),
        Step("tdamp", "text",
             "Coupling time tdamp (fs) (default: auto = 100*timestep, min 20)",
             applies=lambda s: (_thermostat_is(s, "nose_hoover")
                                and s.get("adjust_thermostat")),
             default=None, cast=float, label="tdamp (fs)"),
        Step("tchain", "text", "Chain length tchain",
             applies=lambda s: (_thermostat_is(s, "nose_hoover")
                                and s.get("adjust_thermostat")),
             default=3, cast=int, label="tchain"),
        Step("tloop", "text", "Inner loops tloop",
             applies=lambda s: (_thermostat_is(s, "nose_hoover")
                                and s.get("adjust_thermostat")),
             default=1, cast=int, label="tloop"),

        # --- MD: output -----------------------------------------------------
        Step("traj_interval", "text", "Record every N steps",
             applies=_is_md, default=10, cast=int, label="Record every N steps"),
        Step("traj_format", "select", "Trajectory format?",
             applies=_is_md, choices=[("traj", "traj"), ("xyz", "xyz")],
             default="traj", label="Trajectory format"),
        Step("wrap", "bool", "Also wrap the trajectory at the end?",
             applies=_is_md, default=False, label="Wrap at end"),

        # --- single-point: what to compute + output -------------------------
        Step("sp_forces", "bool",
             "Also compute forces (and stress if periodic)?",
             applies=_is_sp, default=True, label="Compute forces"),
        Step("sp_output", "text", "Results file (extended-xyz)",
             applies=_is_sp, default="singlepoint.extxyz",
             label="Results file"),

        # --- MD: biasing files ----------------------------------------------
        Step("plumed", "path", "PLUMED input file",
             applies=lambda s: _is_md(s) and s.get("biased"),
             label="PLUMED input file"),
        Step("prev_steps", "text", "Previous step count (default: none)",
             applies=lambda s: (_is_md(s) and s.get("biased")
                                and s.get("start_kind") == "restart"),
             default=None, cast=int, label="Previous step count"),

        # --- both branches: script filename ---------------------------------
        Step("output", "text", "Script filename",
             default=_default_output, label="Script filename"),
    ]


# --------------------------------------------------------------------------- #
# engine
# --------------------------------------------------------------------------- #
def _run_flow(q, steps, state, start=0):
    """Walk the steps forward, asking each applicable one, honouring back/quit."""
    i = start
    asked: List[int] = []
    while i < len(steps):
        step = steps[i]
        if not step.applies(state):
            i += 1
            continue
        current = state.get(step.key, step.default_value(state))
        res = step.ask(q, state, current, allow_back=bool(asked))
        if res is EXIT:
            raise typer.Exit()
        if res is BACK:
            if asked:
                i = asked.pop()
            continue
        old = state.get(step.key, _MISSING)
        state[step.key] = res
        if step.clears and res != old:
            for dep in step.clears:
                state.pop(dep, None)
        asked.append(i)
        i += 1


def _reconcile(q, steps, state):
    """After a structural edit: ask any newly-applicable-but-missing steps (in
    order) and drop answers that no longer apply."""
    for step in steps:
        if step.applies(state):
            if step.key not in state:
                res = step.ask(q, state, step.default_value(state),
                               allow_back=False)
                if res is EXIT:
                    raise typer.Exit()
                state[step.key] = res
        else:
            state.pop(step.key, None)


def _review(q, steps, state):
    """Let the user change any editable, applicable field, then write."""
    while True:
        pairs = [("✅  Write the script now", ("write", -1))]
        for idx, step in enumerate(steps):
            if step.editable and step.applies(state):
                pairs.append((f"{step.label}: {step.fmt_value(state)}",
                              ("edit", idx)))
        pairs.append(("✕  Quit without writing", ("quit", -1)))

        choices = [q.Choice(title, value) for title, value in pairs]
        sel = q.select("Review your job — change a field or write the script:",
                       choices=choices).ask()
        if sel is None or sel[0] == "quit":
            raise typer.Exit()
        if sel[0] == "write":
            return
        step = steps[sel[1]]
        current = state.get(step.key, step.default_value(state))
        res = step.ask(q, state, current, allow_back=True)
        if res is EXIT:
            raise typer.Exit()
        if res is BACK:
            continue                      # cancel this edit, back to the review
        old = state.get(step.key, _MISSING)
        state[step.key] = res
        if step.clears and res != old:
            for dep in step.clears:
                state.pop(dep, None)
            _reconcile(q, steps, state)


# --------------------------------------------------------------------------- #
# state -> config
# --------------------------------------------------------------------------- #
def _build_nvt(state) -> NVTConfig:
    kind = state.get("start_kind", "structure")
    path = state.get("start_path")
    cat = state.get("category")
    # Single-point and relax each have one job and no restart; MD carries the
    # chosen ensemble. Relax also uses its own output filenames.
    job = {"singlepoint": "singlepoint", "relax": "relax"}.get(
        cat, state.get("job", "nvt"))
    if cat == "relax":
        traj, log, last_frame = "opt", "opt.log", "optimized.xyz"
    else:
        traj, log, last_frame = "equil", "equil.log", "last_frame.xyz"
    return NVTConfig(
        checkpoint=state.get("checkpoint"),
        calculator=state.get("calculator", "uma"),
        variant=state.get("variant"),
        job=job,
        precision=state.get("precision"),
        dispersion=state.get("dispersion", False),
        external_field=state.get("external_field"),
        orcasimpleinput=state.get("orcasimpleinput", "B3LYP def2-SVP"),
        orcablocks=state.get("orcablocks"),
        nprocs=state.get("nprocs"),
        command=state.get("command"),
        pseudopotentials=state.get("pseudopotentials") or None,
        pseudo_dir=state.get("pseudo_dir"),
        ecutwfc=state.get("ecutwfc"),
        ecutrho=state.get("ecutrho"),
        kpts=state.get("kpts"),
        input_data=state.get("input_data") or None,
        structure=path if kind == "structure" else None,
        restart=path if kind == "restart" else None,
        cell=state.get("cell"),
        pbc=state.get("pbc", "true"),
        charge=state.get("charge", 0),
        multiplicity=state.get("multiplicity", 1),
        temperature=state.get("temperature", 298.15),
        timestep=state.get("timestep", 0.5),
        nsteps=state.get("nsteps", 10000),
        seed=state.get("seed", 42),
        thermostat=state.get("thermostat"),
        tdamp=state.get("tdamp"),
        tchain=state.get("tchain", 3),
        tloop=state.get("tloop", 1),
        friction=state.get("friction"),
        taut=state.get("taut"),
        optimizer=state.get("optimizer"),
        fmax=state.get("fmax", 0.05),
        traj=traj,
        log=log,
        last_frame=last_frame,
        traj_interval=state.get("traj_interval", 10),
        traj_format=state.get("traj_format", "traj"),
        wrap=state.get("wrap", False),
        plumed=state.get("plumed"),
        prev_steps=state.get("prev_steps"),
        sp_forces=state.get("sp_forces", True),
        sp_output=state.get("sp_output", "singlepoint.extxyz"),
    )


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def run() -> None:
    try:
        import questionary
    except ImportError:
        typer.secho("The interactive wizard needs 'questionary' "
                    "(pip install questionary).", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(_TIP, fg=typer.colors.BLUE)
    steps = _build_steps()
    state: dict = {}
    _run_flow(questionary, steps, state)
    _review(questionary, steps, state)

    output = state["output"]
    if state["category"] == "postprocess":
        cfg = WrapConfig(input=state["pp_input"], output=state.get("pp_wrapped"))
        text = generate.generate_wrap_script(cfg, script_name=output)
    elif state["category"] == "singlepoint":
        text = generate.generate_singlepoint_script(_build_nvt(state),
                                                    script_name=output)
    elif state["category"] == "relax":
        text = generate.generate_relax_script(_build_nvt(state),
                                              script_name=output)
    else:
        text = generate.generate_md_script(_build_nvt(state), script_name=output)

    path = generate.write_script(text, output)
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)
    typer.echo(f"Run it with:  python {path}")
    if questionary.confirm("Run it now?", default=False).ask():
        core.execute_script(path)
