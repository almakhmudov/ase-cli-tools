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

# Every calculator is a *family* of one or more variants selected with
# ``--variant``. A variant names its skeleton template, the params it contributes
# and, when it shares a template with its siblings, the fixed param ``values`` it
# bakes in (e.g. a UMA task head or a GRACE model name). A variant flagged
# ``uses_charge_spin`` additionally consumes CHARGE/MULTIPLICITY.

# UMA property heads: one shared template, differing only by the TASK_NAME value.
# 'omol' is the charge/spin head. Built into variants below.
_UMA_TASKS = {
    "oc20": "catalysis",
    "oc22": "oxide catalysis (UMA 1p2 only)",
    "oc25": "(electro)catalysis (UMA 1p2 only)",
    "omat": "inorganic materials",
    "omol": "molecules & polymers (uses charge & spin)",
    "odac": "MOFs",
    "omc":  "molecular crystals",
}

# GRACE foundation models: one shared template, differing only by MODEL. No
# checkpoint file (weights download by name); runs on TensorFlow, not PyTorch.
_GRACE_MODELS = {
    "GRACE-1L-OMAT-medium-ft-E": "1-layer OMAT, medium, fine-tuned (E)",
    "GRACE-1L-OMAT-large-ft-E":  "1-layer OMAT, large, fine-tuned (E)",
    "GRACE-2L-OMAT-medium-ft-E": "2-layer OMAT, medium, fine-tuned (E)",
    "GRACE-2L-OMAT-large-ft-E":  "2-layer OMAT, large, fine-tuned (E)",
    "GRACE-3L-OMAT-large":       "3-layer OMAT, large",
}

# --- calculators ----------------------------------------------------------- #
CALCULATORS = {
    "uma": {
        "label": "UMA (FairChem)",
        # One variant per property head; all share the UMA template and pin
        # TASK_NAME. 'omol' additionally uses the system's charge & spin.
        "variants": {
            name: {
                "label": f"UMA ({name})",
                "desc": desc,
                "template": "calculators/uma.py.tmpl",
                "params": ["CHECKPOINT", "TASK_NAME"],
                "values": {"TASK_NAME": name},
                **({"uses_charge_spin": True} if name == "omol" else {}),
            }
            for name, desc in _UMA_TASKS.items()
        },
        "default_variant": "omol",
    },
    "mace": {
        "label": "MACE",
        # MACE is a family: the user first picks "mace", then one of these
        # variants. Each variant is its own skeleton (a different loader
        # function) with its own parameter set. A variant that sets
        # "uses_charge_spin" additionally consumes CHARGE/MULTIPLICITY. The
        # PRECISION param maps to MACE's ``default_dtype`` in the template; its
        # allowed values and default live in each variant's "precision" spec.
        "variants": {
            "mace_mp": {
                "label": "MACE-MP",
                "desc": "materials foundation model; optional D3 dispersion",
                "template": "calculators/mace/mp.py.tmpl",
                "params": ["CHECKPOINT", "PRECISION", "DISPERSION"],
                "precision": {"default": "float64",
                              "choices": ["float32", "float64"]},
            },
            "mace_off": {
                "label": "MACE-OFF",
                "desc": "organic-molecule foundation model",
                "template": "calculators/mace/off.py.tmpl",
                "params": ["CHECKPOINT", "PRECISION"],
                "precision": {"default": "float64",
                              "choices": ["float32", "float64"]},
            },
            "mace_polar": {
                "label": "MACE-POLAR",
                "desc": "polarisable; uses charge, spin & optional external field",
                "template": "calculators/mace/polar.py.tmpl",
                "params": ["CHECKPOINT", "PRECISION", "CHARGE", "MULTIPLICITY",
                           "EXTERNAL_FIELD"],
                "precision": {"default": "float64",
                              "choices": ["float32", "float64"]},
                "uses_charge_spin": True,
            },
        },
        "default_variant": "mace_mp",
    },
    "orb": {
        "label": "Orb (orb-models)",
        # Orb is a family too. Unlike UMA/MACE it needs no checkpoint file: each
        # variant loads its pretrained weights by name, so no CHECKPOINT param.
        # PRECISION maps to Orb's ``precision`` kwarg; the recommended default is
        # "float32-highest".
        "variants": {
            "orb_v3_omat": {
                "label": "Orb-v3-conservative-inf-omat",
                "desc": "materials foundation model; optional D3 dispersion",
                "template": "calculators/orb/v3_omat.py.tmpl",
                "params": ["PRECISION", "DISPERSION"],
                "precision": {"default": "float32-highest",
                              "choices": ["float32-highest", "float32-high",
                                          "float64"]},
            },
            "orbmol_v2": {
                "label": "OrbMol-v2",
                "desc": "molecules; uses charge & spin",
                "template": "calculators/orb/orbmol_v2.py.tmpl",
                "params": ["PRECISION", "CHARGE", "MULTIPLICITY"],
                "precision": {"default": "float32-highest",
                              "choices": ["float32-highest", "float32-high",
                                          "float64"]},
                "uses_charge_spin": True,
            },
        },
        "default_variant": "orb_v3_omat",
    },
    "grace": {
        "label": "GRACE (tensorpotential)",
        # One variant per foundation model; all share the GRACE template and pin
        # MODEL. No checkpoint file needed.
        "variants": {
            name: {
                "label": name,
                "desc": desc,
                "template": "calculators/grace.py.tmpl",
                "params": ["MODEL"],
                "values": {"MODEL": name},
            }
            for name, desc in _GRACE_MODELS.items()
        },
        "default_variant": "GRACE-1L-OMAT-medium-ft-E",
    },
    "orca": {
        "label": "ORCA (QM)",
        # ORCA is a QM code, not an MLIP: no model file, no variant family and no
        # selectable precision. It is configured by two free-text strings --
        # ORCASIMPLEINPUT (the "!" line) and ORCABLOCKS (the "% ... end" text) --
        # plus the system's charge & spin, which ASE passes straight to the ORCA
        # constructor (so ``uses_charge_spin`` is set to add CHARGE/MULTIPLICITY
        # to the parameter block, but the calculator reads them as constructor
        # args, not from atoms.info). COMMAND optionally overrides the orca binary
        # path via an OrcaProfile. Being variant-less, it has no "variants" dict;
        # ``resolve_variant`` returns this spec directly.
        "params": ["ORCASIMPLEINPUT", "ORCABLOCKS", "COMMAND"],
        "template": "calculators/orca.py.tmpl",
        "uses_charge_spin": True,
    },
    "espresso": {
        "label": "Quantum ESPRESSO (QM)",
        # Plane-wave DFT code. Variant-less like ORCA. Configured by a
        # PSEUDOPOTENTIALS dict (element -> UPF file in PSEUDO_DIR), a flat
        # INPUT_DATA dict of pw.x keywords (ASE routes each to its &section) and a
        # KPTS Monkhorst-Pack grid. COMMAND + PSEUDO_DIR build an EspressoProfile
        # (both or neither). ``uses_charge_spin`` adds CHARGE/MULTIPLICITY to the
        # parameter block; the template maps them to QE keywords (tot_charge /
        # nspin / tot_magnetization). It reads them as pw.x keywords, not from
        # atoms.info, so the template omits the $CHARGE_SPIN placeholder.
        "params": ["PSEUDOPOTENTIALS", "PSEUDO_DIR", "INPUT_DATA", "KPTS",
                   "COMMAND"],
        "template": "calculators/espresso.py.tmpl",
        "uses_charge_spin": True,
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


def uses_charge_spin(calculator: str, variant: "str | None" = None) -> bool:
    """Whether the chosen variant reads the system's charge & spin.

    True for any variant flagged ``uses_charge_spin`` (UMA 'omol', MACE-POLAR,
    OrbMol-v2)."""
    _, comp = resolve_variant(calculator, variant)
    return bool(comp.get("uses_charge_spin"))


def precision_spec(calculator: str, variant: "str | None" = None):
    """Return the ``{"default", "choices"}`` precision spec for a calculator/
    variant, or ``None`` if it does not expose a selectable precision."""
    _, comp = resolve_variant(calculator, variant)
    return comp.get("precision")

# --- thermostats (the dynamics driver for a thermostatted MD job) ---------- #
# A thermostatted job (NVT) picks one of these with ``--thermostat``; each names
# the driver skeleton that builds the ASE ``dyn`` object and the parameter names
# it contributes. Adding a thermostat is a driver template + one entry here.
THERMOSTATS = {
    "nose_hoover": {
        "label": "Nose-Hoover chain",
        "template": "drivers/nvt_nose_hoover.py.tmpl",
        "params": ["TDAMP", "TCHAIN", "TLOOP"],
    },
    "langevin": {
        "label": "Langevin",
        "template": "drivers/nvt_langevin.py.tmpl",
        "params": ["FRICTION"],
    },
    "csvr": {
        "label": "CSVR (Bussi)",
        "template": "drivers/nvt_csvr.py.tmpl",
        "params": ["TAUT"],
    },
}


# --- optimizers (the algorithm for a geometry-optimization job) ------------ #
# A relax job picks one of these with ``--optimizer``. They share one template
# and differ only by the ASE optimizer class spliced into it, so each entry just
# names that class (all live in ``ase.optimize``). Adding one is a single entry.
OPTIMIZERS = {
    "bfgs":  {"label": "BFGS",  "class": "BFGS"},
    "lbfgs": {"label": "LBFGS", "class": "LBFGS"},
    "fire":  {"label": "FIRE",  "class": "FIRE"},
}


def resolve_optimizer(job: str, optimizer: "str | None" = None):
    """Return ``(optimizer_name, optimizer_spec)`` for a job, or ``(None, None)``
    for jobs without an ``optimizers`` dict."""
    spec = JOBS[job]
    optimizers = spec.get("optimizers")
    if not optimizers:
        return None, None
    name = (optimizer or spec.get("default_optimizer")
            or next(iter(optimizers)))
    if name not in optimizers:
        raise KeyError(f"unknown optimizer {name!r} for job {job!r}; "
                       f"available: {sorted(optimizers)}")
    return name, optimizers[name]


def resolve_thermostat(job: str, thermostat: "str | None" = None):
    """Return ``(thermostat_name, thermostat_spec)`` for a job.

    Jobs without a ``thermostats`` dict (e.g. NVE, which has a fixed integrator)
    return ``(None, None)`` so the caller can treat both kinds uniformly."""
    spec = JOBS[job]
    thermostats = spec.get("thermostats")
    if not thermostats:
        return None, None
    name = (thermostat or spec.get("default_thermostat")
            or next(iter(thermostats)))
    if name not in thermostats:
        raise KeyError(f"unknown thermostat {name!r} for job {job!r}; "
                       f"available: {sorted(thermostats)}")
    return name, thermostats[name]

# --- jobs (ensembles / drivers) -------------------------------------------- #
# Every MD job shares the ``jobs/md.py.tmpl`` tail (velocities, reporter, run,
# final frame) and supplies the dynamics driver that builds ``dyn``: a
# thermostatted job lists ``thermostats`` (the driver is chosen with
# ``--thermostat``); a fixed-integrator job names a single ``driver_template``.
JOBS = {
    "nvt": {
        "label": "NVT",
        "category": "md",
        "template": "jobs/md.py.tmpl",
        "thermostats": THERMOSTATS,
        "default_thermostat": "nose_hoover",
        "params": [
            "TEMPERATURE", "TIMESTEP", "NSTEPS", "TRAJ_INTERVAL", "SEED",
            "TRAJ", "LOG", "LAST_FRAME",
        ],
    },
    "nve": {
        "label": "NVE (microcanonical)",
        "category": "md",
        "template": "jobs/md.py.tmpl",
        "driver_template": "drivers/nve.py.tmpl",
        "params": [
            "TEMPERATURE", "TIMESTEP", "NSTEPS", "TRAJ_INTERVAL", "SEED",
            "TRAJ", "LOG", "LAST_FRAME",
        ],
    },
    "singlepoint": {
        "label": "Single-point energy & forces",
        "category": "singlepoint",
        "template": "jobs/singlepoint.py.tmpl",
        # No dynamics: just evaluate the calculator on the loaded structure and
        # write the results. SP_FORCES toggles the (optional) force/stress
        # evaluation; SP_OUTPUT names the extended-xyz results file.
        "params": ["SP_FORCES", "SP_OUTPUT"],
    },
    "relax": {
        "label": "Geometry optimization",
        "category": "relax",
        "template": "jobs/relax.py.tmpl",
        # The optimizer algorithm is chosen with ``--optimizer`` (like the NVT
        # thermostat); its class name is spliced into the template. FMAX is the
        # force-convergence threshold, NSTEPS the iteration cap. Positions only
        # (cell relaxation needs a Filter -- a future addition).
        "optimizers": OPTIMIZERS,
        "default_optimizer": "bfgs",
        "params": ["FMAX", "NSTEPS", "TRAJ", "LOG", "LAST_FRAME"],
    },
    # Future: "npt": {...}
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
