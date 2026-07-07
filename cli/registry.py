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
    # Future: "mace": {...}
}

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
