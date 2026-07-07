# ase-cli-tools

**An interactive command-line toolkit for building and running computational
chemistry jobs with [ASE](https://wiki.fysik.dtu.dk/ase/).**

`ase-cli-tools` lets you assemble a tested ASE job — pick a calculator, an
ensemble/driver and any extras — either by answering a few arrow-key prompts or
by passing flags. It then writes a **self-contained, runnable Python script**
with your parameters baked in, which you can run immediately, submit to an HPC
queue, version-control and publish. The script *is* the record of what ran, so
your work stays reproducible.

The building blocks are predefined and tested, and assembled from modular
skeletons — in the spirit of ASE's own swappable calculators and dynamics. The
first release covers **NVT molecular dynamics with the UMA potential**
(from [FairChem](https://github.com/facebookresearch/fairchem)), with optional
PLUMED biasing; more calculators, ensembles and job types are on the roadmap.

> **Status:** early prototype. Interfaces may change.

## Why use it

- **Interactive.** Run `ase-cli-tools` with no arguments and choose everything
  from arrow-key menus, typing paths, charge, temperature and so on as you go.
- **Scriptable.** The same jobs are available as plain flags for batch scripts
  and automation.
- **Reproducible.** Every job produces a standalone `.py` script with parameters
  (and any PLUMED input) embedded — keep it, rerun it, publish it.
- **Modular.** Calculators, ensembles and features are separate skeleton files
  listed in a registry; adding a new one does not touch the assembler.

## Installation

The recommended path is a conda environment (PLUMED and its Python interface are
easiest to obtain from conda-forge):

```bash
conda env create -f environment.yml
conda activate ase-cli-tools
pip install -e .            # installs the `ase-cli-tools` command
```

For a CPU-only machine, replace the CUDA PyTorch wheel with the CPU build:

```bash
pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
```

## Usage

### Interactive (arrow keys)

Just run the command with no arguments and follow the prompts:

```bash
ase-cli-tools
```

You pick the job category, the ensemble, whether to bias it, the calculator and
its task (property head) from menus, then type the remaining parameters. Charge
and spin are only asked for the molecular `omol` task, and the Nose-Hoover
thermostat parameters are behind an optional step so you can leave them at their
defaults. The tool writes the script and offers to run it.

### Flag-driven (scriptable)

```bash
# generate an NVT script (writes run_nvt.py)
ase-cli-tools md run --job nvt -c uma.pt -s mixture.xyz --cell "20 20 20" \
    --charge 0 --multiplicity 2 -T 498.15 -n 10000 --wrap

# the same job, biased: --plumed turns on biasing (input embedded in the script)
ase-cli-tools md run --job nvt -p examples/plumed.dat -c uma.pt \
    -s mixture.xyz --cell "20 20 20" -T 498.15 -n 500000

# a non-molecular UMA task (no charge/spin): choose the property head with --task
ase-cli-tools md run --job nvt -c uma.pt -s crystal.cif --cell "10 10 10" \
    --task omat -T 300 -n 20000

# custom Nose-Hoover thermostat (omitted values fall back to the defaults)
ase-cli-tools md run --job nvt -c uma.pt -s mixture.xyz --cell "20 20 20" \
    -T 498.15 -n 10000 --tdamp 50 --tchain 5 --tloop 2

# wrap post-processing
ase-cli-tools postprocess wrap equil.traj

# preview instead of writing, or run immediately after writing
ase-cli-tools md run --job nvt -c uma.pt -s in.xyz --stdout
ase-cli-tools md run --job nvt -c uma.pt -s in.xyz --run
```

Every job writes a `.py` file by default (`-o` to name it), `--stdout` prints it
instead, and `--run` executes the freshly written script.

`--task` selects the UMA property head: `oc20` (catalysis), `oc22` (oxide
catalysis, 1p2 only), `oc25` ((electro)catalysis, 1p2 only), `omat` (inorganic
materials), `omol` (molecules & polymers, the default), `odac` (MOFs) and `omc`
(molecular crystals). Only `omol` uses `--charge`/`--multiplicity`; they are
ignored for the other tasks. The Nose-Hoover thermostat is tunable via
`--tdamp` (coupling time in fs; omit for the auto value of `100*timestep`, at
least 20 fs), `--tchain` (chain length) and `--tloop` (inner loops).

Command layout:

```
ase-cli-tools
├── md
│   └── run          -> generate an MD script; --job selects the ensemble
│                       (nvt, ...), --task the UMA head, --plumed biasing
└── postprocess
    └── wrap         -> generate a trajectory-wrapping script
```

Biasing is an **option on a job** (`--plumed`), not a job of its own — so it
applies uniformly to any ensemble as they are added.

## Roadmap

Calculators:

- [ ] MACE
- [ ] GRACE
- [ ] ORCA
- [ ] CP2K
- [ ] Quantum ESPRESSO

Jobs / ensembles:

- [ ] More thermostats and barostats
- [ ] NVE and NPT molecular dynamics
- [ ] Single-point and frequency (vibrational) calculations
- [ ] Geometry and cell relaxation
