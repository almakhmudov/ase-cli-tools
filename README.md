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
skeletons — in the spirit of ASE's own swappable calculators and dynamics. Now
the toolkit covers **NVT molecular dynamics** with the **UMA**
(from [FairChem](https://github.com/facebookresearch/fairchem)) and **MACE**
potentials, with optional PLUMED biasing; more calculators, ensembles and job
types are on the roadmap.

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

### MACE backend (optional)

The MACE calculators (`--calculator mace`) need the MACE suite. Install it from
source; the MACE-POLAR variant additionally needs `graph_electrostatics`:

```bash
git clone https://github.com/ACEsuit/mace.git
cd mace && git checkout main && pip install .
pip install git+https://github.com/WillBaldwin0/graph_electrostatics.git   # MACE-POLAR only
```

## Usage

### Interactive (arrow keys)

Just run the command with no arguments and follow the prompts:

```bash
ase-cli-tools
```

You pick the job category, the ensemble, whether to bias it, the calculator and
its task (property head) from menus, then type the remaining parameters —
periodicity, cell, temperature, timestep, steps, recording interval and so on.
Every typed prompt shows its default in parentheses and accepts a blank to use
it. Charge and spin are only asked for the molecular `omol` task (and
MACE-POLAR), and the Nose-Hoover thermostat parameters are behind an optional
step so you can leave them at their defaults. The wizard now exposes the same
options as the flags, so nothing is reachable by flag alone.

You are never locked into a linear path. At **every** step you can go **back** to
change the previous answer — including the job type — or **quit** the whole
process: pick `↩ go back` / `✕ quit` on a menu, or type `<` / `!q` at a typed
prompt (Esc also quits). When all the questions are answered, a **review** screen
lists every field and its value; pick any one to change it (the job type aside,
since it decides which questions exist) and you return to the review. Changing a
structural choice — say the calculator — re-asks only the answers that depend on
it. When it looks right, choose *Write the script now*, and the tool writes it
and offers to run it.

### Flag-driven (scriptable)

```bash
# generate an NVT script (writes run_nvt.py)
ase-cli-tools md run --job nvt -c uma.pt -s mixture.xyz --cell "20 20 20" \
    --charge 0 --multiplicity 2 -T 498.15 -n 10000 --wrap

# the same job, biased: --plumed turns on biasing (input embedded in the script)
ase-cli-tools md run --job nvt -p plumed.dat -c uma.pt \
    -s mixture.xyz --cell "20 20 20" -T 498.15 -n 500000

# a non-molecular UMA task (no charge/spin): choose the property head with --task
ase-cli-tools md run --job nvt -c uma.pt -s crystal.cif --cell "10 10 10" \
    --task omat -T 300 -n 20000

# custom Nose-Hoover thermostat (omitted values fall back to the defaults)
ase-cli-tools md run --job nvt -c uma.pt -s mixture.xyz --cell "20 20 20" \
    -T 498.15 -n 10000 --tdamp 50 --tchain 5 --tloop 2

# MACE instead of UMA: pick a variant with --variant (default mace_mp)
ase-cli-tools md run --job nvt --calculator mace --variant mace_mp -c mace.model \
    -s mixture.xyz --cell "20 20 20" --dtype float32 --dispersion True -T 298.15 -n 10000

# MACE-POLAR: uses charge/spin and an optional external field
ase-cli-tools md run --job nvt --calculator mace --variant mace_polar -c polar.model \
    -s mixture.xyz --cell "20 20 20" --charge 0 --multiplicity 1 \
    --external-field "0 0 0.01" -T 298.15 -n 10000

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

`--calculator` chooses the backend (`uma`, `mace`), with `-c`/`--checkpoint`
pointing at that backend's model file. For MACE, `--variant` selects the family
member: `mace_mp` (materials, the default; `--dispersion True` adds a D3
correction), `mace_off` (organic molecules) and `mace_polar` (uses
`--charge`/`--multiplicity` and an optional `--external-field "Ex Ey Ez"`).
`--task` applies to UMA only, and `--dtype` (`float32`/`float64`) to MACE. Each
backend or variant is a small template plus one registry entry, so biasing, the
thermostat and the output options work the same across all of them.

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

- [x] MACE
- [ ] GRACE
- [ ] ORCA
- [ ] CP2K
- [ ] Quantum ESPRESSO

Jobs / ensembles:

- [ ] More thermostats and barostats
- [ ] NVE and NPT molecular dynamics
- [ ] Single-point and frequency (vibrational) calculations
- [ ] Geometry and cell relaxation
