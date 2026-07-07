"""Registry of the building blocks ase-cli-tools can assemble.

This is the modular seam: a calculator, a job (ensemble/driver) or a feature is
described here by (a) a human label, (b) the skeleton template that implements
it, and (c) the parameter names it contributes to the generated script's
parameter block. Adding a new calculator or job means dropping a template file
under ``templates/`` and adding one entry here -- no changes to the assembler.

Each parameter name must have a renderer in ``assembler.PARAM_VALUES`` so its
value can be baked into the generated script.
"""

from __future__ import annotations

# Shared parameters emitted for every job (structure loading + device).
SHARED_PARAMS = [
    "STRUCTURE", "RESTART", "DEVICE",
    "CELL", "PBC",
]

# --- calculators ----------------------------------------------------------- #
CALCULATORS = {
    "uma": {
        "label": "UMA (FairChem)",
        "template": "calculators/uma.py.tmpl",
        "params": ["CHECKPOINT", "TASK_NAME"],
        # UMA property heads. The user picks one; each targets a domain.
        "tasks": {
            "oc20": "catalysis",
            "oc22": "oxide catalysis (UMA 1p2 only)",
            "oc25": "(electro)catalysis (UMA 1p2 only)",
            "omat": "inorganic materials",
            "omol": "molecules & polymers (uses charge & spin)",
            "odac": "MOFs",
            "omc":  "molecular crystals",
        },
        # This task additionally consumes the CHARGE and MULTIPLICITY params;
        # for every other task they are neither prompted nor written.
        "charge_spin_task": "omol",
    },
    "mace": {
        "label": "MACE",
        # MACE is a family: the user first picks "mace", then one of these
        # variants. Each variant is its own skeleton (a different loader
        # function) with its own parameter set. A variant that sets
        # "uses_charge_spin" additionally consumes CHARGE/MULTIPLICITY.
        "variants": {
            "mace_mp": {
                "label": "MACE-MP",
                "desc": "materials foundation model; optional D3 dispersion",
                "template": "calculators/mace/mp.py.tmpl",
                "params": ["CHECKPOINT", "DTYPE", "DISPERSION"],
            },
            "mace_off": {
                "label": "MACE-OFF",
                "desc": "organic-molecule foundation model",
                "template": "calculators/mace/off.py.tmpl",
                "params": ["CHECKPOINT", "DTYPE"],
            },
            "mace_polar": {
                "label": "MACE-POLAR",
                "desc": "polarisable; uses charge, spin & optional external field",
                "template": "calculators/mace/polar.py.tmpl",
                "params": ["CHECKPOINT", "DTYPE", "CHARGE", "MULTIPLICITY",
                           "EXTERNAL_FIELD"],
                "uses_charge_spin": True,
            },
        },
        "default_variant": "mace_mp",
    },
}


# --------------------------------------------------------------------------- #
# helpers for calculators that expose variants (e.g. MACE) or charge/spin
# --------------------------------------------------------------------------- #
def resolve_variant(calculator: str, variant: "str | None" = None):
    """Return ``(variant_name, component_spec)`` for a calculator.

    Calculators without a ``variants`` dict return ``(None, calc_spec)`` so the
    caller can treat variant-less and variant-bearing calculators uniformly.
    """
    calc = CALCULATORS[calculator]
    variants = calc.get("variants")
    if not variants:
        return None, calc
    name = variant or calc.get("default_variant") or next(iter(variants))
    if name not in variants:
        raise KeyError(f"unknown variant {name!r} for calculator "
                       f"{calculator!r}; available: {sorted(variants)}")
    return name, variants[name]


def uses_charge_spin(calculator: str, task_name: "str | None" = None,
                     variant: "str | None" = None) -> bool:
    """Whether this calculator/task/variant combination reads charge & spin.

    True for UMA's designated ``charge_spin_task`` (omol) and for any variant
    flagged ``uses_charge_spin`` (MACE-POLAR).
    """
    calc = CALCULATORS[calculator]
    _, comp = resolve_variant(calculator, variant)
    if comp.get("uses_charge_spin"):
        return True
    cs_task = calc.get("charge_spin_task")
    return cs_task is not None and task_name == cs_task

# --- jobs (ensembles / drivers) -------------------------------------------- #
JOBS = {
    "nvt": {
        "label": "NVT (Nose-Hoover chain)",
        "category": "md",
        "template": "jobs/nvt.py.tmpl",
        "params": [
            "TEMPERATURE", "TIMESTEP", "NSTEPS", "TRAJ_INTERVAL",
            "TDAMP", "TCHAIN", "TLOOP", "SEED",
            "TRAJ", "LOG", "LAST_FRAME",
        ],
    },
    # Future: "npt": {...}, "relax": {...}
}

# --- optional features layered onto a job ---------------------------------- #
FEATURES = {
    "plumed": {
        "label": "PLUMED bias",
        "attach_template": "attach/plumed.py.tmpl",   # replaces the plain attach
        "params": ["PLUMED_LOG", "PREV_STEPS"],        # PLUMED_INPUT added as a block
    },
    "wrap": {
        "label": "Wrap trajectory at end",
        "append_template": "features/wrap.py.tmpl",
        "params": ["WRAPPED"],
    },
}

# --- post-processing jobs (standalone scripts) ----------------------------- #
POSTPROCESS = {
    "wrap": {
        "label": "Wrap trajectory frames into the cell",
        "template": "postprocess/wrap.py.tmpl",
    },
}
