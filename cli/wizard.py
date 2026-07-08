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


def _is_pp(s):
    return s.get("category") == "postprocess"


def _comp_params(s):
    calc = s.get("calculator")
    if not calc:
        return []
    _, comp = registry.resolve_variant(calc, s.get("variant"))
    return comp["params"]


def _has_variants(s):
    calc = s.get("calculator")
    return bool(calc and registry.CALCULATORS[calc].get("variants"))


def _has_tasks(s):
    calc = s.get("calculator")
    return bool(calc and registry.CALCULATORS[calc].get("tasks"))


def _uses_cs(s):
    calc = s.get("calculator")
    if not calc:
        return False
    return registry.uses_charge_spin(calc, s.get("task_name"), s.get("variant"))


# --------------------------------------------------------------------------- #
# choice builders (depend on earlier answers)
# --------------------------------------------------------------------------- #
def _calc_pairs(_s):
    return [(spec["label"], name) for name, spec in registry.CALCULATORS.items()]


def _variant_pairs(s):
    variants = registry.CALCULATORS[s["calculator"]]["variants"]
    return [(f"{v['label']} - {v['desc']}", name) for name, v in variants.items()]


def _variant_default(s):
    return registry.CALCULATORS[s["calculator"]].get("default_variant")


def _task_pairs(s):
    tasks = registry.CALCULATORS[s["calculator"]]["tasks"]
    return [(f"{name} - {desc}", name) for name, desc in tasks.items()]


def _task_default(s):
    calc = registry.CALCULATORS[s["calculator"]]
    tasks = calc["tasks"]
    cs = calc.get("charge_spin_task")
    return cs if cs in tasks else next(iter(tasks))


def _has_models(s):
    calc = s.get("calculator")
    return bool(calc and registry.CALCULATORS[calc].get("models"))


def _model_pairs(s):
    models = registry.CALCULATORS[s["calculator"]]["models"]
    return [(f"{name} - {desc}", name) for name, desc in models.items()]


def _model_default(s):
    calc = registry.CALCULATORS[s["calculator"]]
    return calc.get("default_model") or next(iter(calc["models"]))


def _md_job_pairs(_s):
    return [(spec["label"], name) for name, spec in registry.JOBS.items()
            if spec.get("category") == "md"]


def _precision_spec(s):
    return (registry.precision_spec(s["calculator"], s.get("variant"))
            or {"default": None, "choices": []})


def _precision_default(s):
    return _precision_spec(s)["default"]


def _precision_pairs(s):
    spec = _precision_spec(s)
    return [(f"{c} (recommended)" if c == spec["default"] else c, c)
            for c in spec["choices"]]


def _default_output(s):
    if s.get("category") == "postprocess":
        return "run_wrap.py"
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

        # --- MD: calculator + variant/task ----------------------------------
        Step("biased", "bool",
             "Add a PLUMED bias (metadynamics, walls, ...)?",
             applies=_is_md, default=False, label="PLUMED bias",
             clears=("plumed", "prev_steps")),
        Step("calculator", "select", "Calculator?",
             applies=_is_md, choices=_calc_pairs, default="uma",
             label="Calculator",
             clears=("variant", "checkpoint", "task_name", "model", "precision",
                     "dispersion", "charge", "multiplicity", "external_field")),
        Step("variant", "select",
             lambda s: f"{registry.CALCULATORS[s['calculator']]['label']} variant?",
             applies=lambda s: _is_md(s) and _has_variants(s),
             choices=_variant_pairs, default=_variant_default,
             label="Variant",
             clears=("checkpoint", "precision", "dispersion", "charge",
                     "multiplicity", "external_field")),
        Step("checkpoint", "path",
             lambda s: f"Path to the {s.get('variant') or s['calculator']} "
                       f"model/checkpoint file",
             applies=lambda s: _is_md(s) and "CHECKPOINT" in _comp_params(s),
             label="Model/checkpoint"),
        Step("task_name", "select", "Task (property head)?",
             applies=lambda s: _is_md(s) and _has_tasks(s),
             choices=_task_pairs, default=_task_default, label="UMA task",
             clears=("charge", "multiplicity")),
        Step("model", "select", "Foundation model?",
             applies=lambda s: _is_md(s) and _has_models(s),
             choices=_model_pairs, default=_model_default, label="GRACE model"),
        Step("precision", "select", "Model precision?",
             applies=lambda s: _is_md(s) and "PRECISION" in _comp_params(s),
             choices=_precision_pairs, default=_precision_default,
             label="Precision"),
        Step("dispersion", "bool", "Add a D3 dispersion correction?",
             applies=lambda s: _is_md(s) and "DISPERSION" in _comp_params(s),
             default=False, label="D3 dispersion"),

        # --- MD: structure --------------------------------------------------
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
             applies=_is_md, label="Start file"),
        Step("cell", "text", "Cell 'a b c' (default: use the file's cell)",
             applies=_is_md, default=None, label="Cell"),
        Step("pbc", "text", "Periodicity (true/false, or 'T T F' per axis)",
             applies=_is_md, default="true", label="Periodicity (pbc)"),

        # --- MD: charge / spin / field (only when the calc/task uses them) --
        Step("charge", "text", "Charge",
             applies=lambda s: _is_md(s) and _uses_cs(s), default=0, cast=int,
             label="Charge"),
        Step("multiplicity", "text", "Spin multiplicity (2S+1)",
             applies=lambda s: _is_md(s) and _uses_cs(s), default=1, cast=int,
             label="Spin multiplicity"),
        Step("external_field", "text",
             "External field 'Ex Ey Ez' (default: none)",
             applies=lambda s: (_is_md(s) and _uses_cs(s)
                                and "EXTERNAL_FIELD" in _comp_params(s)),
             default=None, label="External field"),

        # --- MD: dynamics ---------------------------------------------------
        Step("temperature", "text", "Temperature (K)",
             applies=_is_md, default=298.15, cast=float, label="Temperature (K)"),
        Step("timestep", "text", "Timestep (fs)",
             applies=_is_md, default=0.5, cast=float, label="Timestep (fs)"),
        Step("nsteps", "text", "Number of steps",
             applies=_is_md, default=10000, cast=int, label="Number of steps"),
        Step("seed", "text", "RNG seed for the initial velocities",
             applies=_is_md, default=42, cast=int, label="RNG seed"),

        # --- MD: thermostat (behind an opt-in) ------------------------------
        Step("adjust_thermostat", "bool",
             "Adjust Nose-Hoover thermostat parameters?",
             applies=_is_md, default=False, label="Custom thermostat",
             clears=("tdamp", "tchain", "tloop")),
        Step("tdamp", "text",
             "Coupling time tdamp (fs) (default: auto = 100*timestep, min 20)",
             applies=lambda s: _is_md(s) and s.get("adjust_thermostat"),
             default=None, cast=float, label="tdamp (fs)"),
        Step("tchain", "text", "Chain length tchain",
             applies=lambda s: _is_md(s) and s.get("adjust_thermostat"),
             default=3, cast=int, label="tchain"),
        Step("tloop", "text", "Inner loops tloop",
             applies=lambda s: _is_md(s) and s.get("adjust_thermostat"),
             default=1, cast=int, label="tloop"),

        # --- MD: output -----------------------------------------------------
        Step("traj_interval", "text", "Record every N steps",
             applies=_is_md, default=10, cast=int, label="Record every N steps"),
        Step("traj_format", "select", "Trajectory format?",
             applies=_is_md, choices=[("traj", "traj"), ("xyz", "xyz")],
             default="traj", label="Trajectory format"),
        Step("wrap", "bool", "Also wrap the trajectory at the end?",
             applies=_is_md, default=False, label="Wrap at end"),

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
    return NVTConfig(
        checkpoint=state.get("checkpoint"),
        calculator=state.get("calculator", "uma"),
        variant=state.get("variant"),
        job=state.get("job", "nvt"),
        task_name=state.get("task_name", "omol"),
        model=state.get("model"),
        precision=state.get("precision"),
        dispersion=state.get("dispersion", False),
        external_field=state.get("external_field"),
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
        tdamp=state.get("tdamp"),
        tchain=state.get("tchain", 3),
        tloop=state.get("tloop", 1),
        traj_interval=state.get("traj_interval", 10),
        traj_format=state.get("traj_format", "traj"),
        wrap=state.get("wrap", False),
        plumed=state.get("plumed"),
        prev_steps=state.get("prev_steps"),
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
    else:
        text = generate.generate_md_script(_build_nvt(state), script_name=output)

    path = generate.write_script(text, output)
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)
    typer.echo(f"Run it with:  python {path}")
    if questionary.confirm("Run it now?", default=False).ask():
        core.execute_script(path)
