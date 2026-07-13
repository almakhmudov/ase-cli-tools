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
the toolkit covers **NVT** (Nose-Hoover, Langevin or CSVR) and **NVE molecular
dynamics**, **single-point** energy & forces and **geometry optimization**
(BFGS/LBFGS/FIRE), with the **UMA**
(from [FairChem](https://github.com/facebookresearch/fairchem)), **MACE**
(from [ACEsuit](https://github.com/ACEsuit/mace)), **Orb**
(from [orb-models](https://github.com/orbital-materials/orb-models)) and **GRACE**
(from [tensorpotential](https://github.com/ICAMS/grace-tensorpotential))
potentials, with optional PLUMED biasing. It also drives the **ORCA** and
**Quantum ESPRESSO** QM codes and can generate **single-point** energy & forces
and **geometry optimization** jobs for any calculator. More calculators,
ensembles and job types are on the roadmap.

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

The base tool is lightweight — it only needs Python, [ASE](https://wiki.fysik.dtu.dk/ase/),
Typer and questionary, and that is enough to **generate** job scripts:

```bash
pip install -e .            # installs the `ase-cli-tools` command
```

Every calculator and the PLUMED biasing are **optional** backends: you only need
them to **run** the generated script for that kind of job. Install just the ones
you use (below), or, for a full tested environment with everything at once, use
the bundled conda file:

```bash
conda env create -f environment.yml   # base tool + all backends + PLUMED
conda activate ase-cli-tools
pip install -e .
```

### UMA backend (optional)

The UMA calculator (`--calculator uma`, the default) needs FairChem and PyTorch:

```bash
pip install fairchem-core==2.21.0 torch==2.8.0
```

The `torch==2.8.0` wheel pulls the CUDA build. For a CPU-only machine, use the
CPU wheel instead:

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

### Orb backend (optional)

The Orb calculators (`--calculator orb`) need the `orb-models` package. Model
weights are downloaded by name on first use, so no checkpoint file is required:

```bash
pip install orb-models
```

### GRACE backend (optional)

The GRACE calculator (`--calculator grace`) needs `tensorpotential` (which pulls
in TensorFlow). Weights download by name on first use, so no checkpoint file is
required:

```bash
pip install tensorpotential
# GPU TensorFlow (optional): pip install "tensorflow[and-cuda]<2.20"
```

### ORCA backend (optional)

The ORCA calculator (`--calculator orca`) talks to the **ORCA** quantum-chemistry
program, which is closed-source but free for academics — register on the
[ORCA forum](https://orcaforum.kofo.mpg.de/) to download the binaries. There is
no pip package: install ORCA yourself and make sure ASE can find the `orca`
binary, either by putting it on your `PATH`, configuring it in ASE's config file,
or passing `--command /full/path/to/orca` (which builds an `OrcaProfile`).
ASE is already a base dependency, so nothing else is needed to generate ORCA
scripts.

### Quantum ESPRESSO backend (optional)

The Quantum ESPRESSO calculator (`--calculator espresso`) talks to the open-source
[Quantum ESPRESSO](https://www.quantum-espresso.org/) `pw.x` executable, which
you install yourself (conda-forge `qe`, a distro package, or from source). You
also need pseudopotential UPF files — a good curated set is
[SSSP](https://www.materialscloud.org/discover/sssp/table/efficiency). Point the
tool at both with `--command /path/to/pw.x` (add an `mpiexec -n N` prefix for
parallel runs) and `--pseudo-dir /path/to/pseudopotentials`, or configure
`espresso` in ASE's config file and omit both. Only ASE is needed to generate the
scripts.

### PLUMED biasing (optional)

Biased runs (`--plumed`) need PLUMED's Python interface, easiest from
conda-forge:

```bash
conda install -c conda-forge py-plumed=2.9.2
```

## Usage

### Interactive (arrow keys)

Just run the command with no arguments and follow the prompts:

```bash
ase-cli-tools
```

You pick the job category, the ensemble, whether to bias it, the calculator and
its variant (the family member — a MACE/Orb variant, a UMA head, or a GRACE
model) and, where it applies, the precision from menus, then type the remaining
parameters — periodicity, cell, temperature, timestep, steps, recording interval
and so on. Every typed prompt shows its default in parentheses and accepts a
blank to use it. Charge and spin are only asked when the chosen calculator uses them
(ORCA, UMA `omol`, MACE-POLAR and Orb-v3-omol), and the Nose-Hoover thermostat parameters
are behind an optional step so you can leave them at their defaults. The wizard
exposes the same options as the flags, so nothing is reachable by flag alone.

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

# a non-molecular UMA head (no charge/spin): choose it with --variant
ase-cli-tools md run --job nvt -c uma.pt -s crystal.cif --cell "10 10 10" \
    --variant omat -T 300 -n 20000

# custom Nose-Hoover thermostat (omitted values fall back to the defaults)
ase-cli-tools md run --job nvt -c uma.pt -s mixture.xyz --cell "20 20 20" \
    -T 498.15 -n 10000 --tdamp 50 --tchain 5 --tloop 2

# a different NVT thermostat: Langevin (--friction) or CSVR/Bussi (--taut)
ase-cli-tools md run --job nvt --thermostat langevin -c uma.pt \
    -s mixture.xyz --cell "20 20 20" -T 498.15 -n 10000 --friction 0.02
ase-cli-tools md run --job nvt --thermostat csvr -c uma.pt \
    -s mixture.xyz --cell "20 20 20" -T 498.15 -n 10000 --taut 80

# NVE (microcanonical): no thermostat; -T only sets the initial velocities
ase-cli-tools md run --job nve -c uma.pt -s mixture.xyz --cell "20 20 20" \
    -T 300 -n 10000

# MACE instead of UMA: pick a variant with --variant (default mace_mp)
ase-cli-tools md run --job nvt --calculator mace --variant mace_mp -c mace.model \
    -s mixture.xyz --cell "20 20 20" --precision float32 --dispersion True -T 298.15 -n 10000

# MACE-POLAR: uses charge/spin and an optional external field
ase-cli-tools md run --job nvt --calculator mace --variant mace_polar -c polar.model \
    -s mixture.xyz --cell "20 20 20" --charge 0 --multiplicity 1 \
    --external-field "0 0 0.01" -T 298.15 -n 10000

# Orb (no checkpoint file needed): default variant orb_v3_omat
ase-cli-tools md run --job nvt --calculator orb -s crystal.cif --cell "10 10 10" \
    --precision float32-highest -T 300 -n 20000

# Orb-v3-omol: molecular model, uses charge/spin (like MACE-POLAR / UMA omol)
ase-cli-tools md run --job nvt --calculator orb --variant orb_v3_omol \
    -s molecule.xyz --charge 0 --multiplicity 1 --precision float32-high -T 298.15 -n 10000

# GRACE (no checkpoint file needed): pick a foundation model with --variant
ase-cli-tools md run --job nvt --calculator grace --variant GRACE-2L-OMAT-large-ft-E \
    -s crystal.cif --cell "10 10 10" -T 300 -n 20000

# single-point energy & forces (any calculator) -> writes singlepoint.extxyz
ase-cli-tools sp run --calculator uma -c uma.pt -s mol.xyz --pbc false
# energy only (cheaper; skips forces and stress)
ase-cli-tools sp run --calculator uma -c uma.pt -s mol.xyz --pbc false --forces False

# single-point with ORCA (QM): --orcasimpleinput is the "!" line, --nprocs sets
# the %pal block, and --orcablock adds a "% ... end" block (repeat for several)
ase-cli-tools sp run --calculator orca -s mol.xyz --pbc false \
    --charge 0 --multiplicity 1 --nprocs 16 \
    --orcasimpleinput "B3LYP def2-TZVP D3BJ TightSCF" \
    --orcablock "%scf maxiter 300 end" --orcablock "%method integrationgrid 3 end"

# ORCA can also drive MD (ab-initio MD): same ORCA flags on `md run`
ase-cli-tools md run --job nvt --calculator orca -s mol.xyz --pbc false \
    --orcasimpleinput "BP86 def2-SVP" --nprocs 16 -T 300 -n 500

# Quantum ESPRESSO single point: pseudopotentials + cutoffs + k-points
ase-cli-tools sp run --calculator espresso -s NaCl.cif --cell "5.64 5.64 5.64" \
    --command "mpiexec -n 16 /path/to/pw.x" --pseudo-dir /path/to/pseudos \
    --pseudo "Na=na_pbe_v1.5.uspp.F.UPF" --pseudo "Cl=cl_pbe_v1.4.uspp.F.UPF" \
    --ecutwfc 60 --ecutrho 480 --kpts "4 4 4" --input "occupations=smearing" \
    --input "smearing=mv" --input "degauss=0.01"

# QE geometry optimization (same calculator flags, on `relax run`)
ase-cli-tools relax run --calculator espresso -s NaCl.cif --cell "5.64 5.64 5.64" \
    --pseudo "Na=na.UPF" --pseudo "Cl=cl.UPF" --ecutwfc 60 --kpts "4 4 4" \
    --optimizer lbfgs --fmax 0.02

# QE with charge & spin: --multiplicity 3 sets nspin=2, tot_magnetization=2
ase-cli-tools sp run --calculator espresso -s O2.xyz --cell "12 12 12" \
    --pseudo "O=o.UPF" --ecutwfc 60 --charge 0 --multiplicity 3

# geometry optimization (positions only): pick the algorithm and force threshold
ase-cli-tools relax run --calculator uma -c uma.pt -s mol.xyz --pbc false \
    --optimizer bfgs --fmax 0.05
# LBFGS/FIRE and a tighter threshold; ORCA relaxation works too (adds EnGrad)
ase-cli-tools relax run --calculator orca -s mol.xyz --pbc false \
    --optimizer fire --fmax 0.02 --nprocs 16 --orcasimpleinput "B3LYP def2-SVP"

# wrap post-processing
ase-cli-tools postprocess wrap equil.traj

# preview instead of writing, or run immediately after writing
ase-cli-tools md run --job nvt -c uma.pt -s in.xyz --stdout
ase-cli-tools md run --job nvt -c uma.pt -s in.xyz --run
```

Every job writes a `.py` file by default (`-o` to name it), `--stdout` prints it
instead, and `--run` executes the freshly written script.

`--job` selects the ensemble: `nvt` (thermostatted, the default) or `nve`
(microcanonical, energy-conserving — `-T` there only sets the initial
velocities). For NVT, `--thermostat` picks the thermostat:

- **`nose_hoover`** (the default) — Nose-Hoover chain, tuned via `--tdamp`
  (coupling time in fs; omit for the auto value of `100*timestep`, at least
  20 fs), `--tchain` (chain length) and `--tloop` (inner loops).
- **`langevin`** — stochastic thermostat, coupling set by `--friction` in fs⁻¹
  (default `0.01`, i.e. 10 ps⁻¹). A typical range is `0.001`–`0.1` fs⁻¹
  (1–100 ps⁻¹). The generated script converts to ASE's internal units for you.
- **`csvr`** — canonical sampling through velocity rescaling
  (Bussi-Donadio-Parrinello), coupling time set by `--taut` (fs; omit for the
  same auto value as `--tdamp`).

`--seed` sets the RNG seed for the initial Maxwell-Boltzmann velocities (default
`42`) so a fresh-start run is reproducible.

`--calculator` chooses the backend (`uma`, `mace`, `orb`, `grace`, `orca`,
`espresso`), with `-c`/`--checkpoint` pointing at that backend's model file where
one is needed (MACE and UMA need one; Orb, GRACE, ORCA and Quantum ESPRESSO do
not — the QM codes use pseudopotentials/basis sets instead).
Every backend is a family, and **`--variant` (`-t`) picks the family member** —
its meaning depends on the calculator:

- **UMA** — the property head: `oc20` (catalysis), `oc22`/`oc25` (1p2 only),
  `omat` (inorganic materials), `omol` (molecules & polymers, the default;
  the only UMA head that uses `--charge`/`--multiplicity`), `odac` (MOFs), `omc`
  (molecular crystals).
- **MACE** — `mace_mp` (materials, the default; `--dispersion True` adds a D3
  correction), `mace_off` (organic molecules) or `mace_polar` (uses
  `--charge`/`--multiplicity` and an optional `--external-field "Ex Ey Ez"`).
- **Orb** — `orb_v3_omat` (Orb-v3-conservative-inf-omat, the default; materials)
  or `orb_v3_omol` (Orb-v3-conservative-omol; molecules, uses
  `--charge`/`--multiplicity`). Both load weights by name (no checkpoint file)
  and need `orb-models >= 0.5`.
- **GRACE** — a foundation model: `GRACE-1L-OMAT-medium-ft-E` (default),
  `GRACE-1L-OMAT-large-ft-E`, `GRACE-2L-OMAT-medium-ft-E`,
  `GRACE-2L-OMAT-large-ft-E` or `GRACE-3L-OMAT-large` (runs on TensorFlow).
- **ORCA** — the QM code; no variants. Configured by the ASE keywords
  `--orcasimpleinput` (the `!` line, default `B3LYP def2-SVP`) and repeated
  `--orcablock` (`% … end` blocks, joined with newlines). `--nprocs N` emits the
  `%pal nprocs N end` block for you, `--charge`/`--multiplicity` go straight to
  ORCA, and `--command` sets the binary path. Add dispersion in
  `--orcasimpleinput` (e.g. `D3BJ`), not via `--dispersion`.
- **Quantum ESPRESSO** — plane-wave DFT (`pw.x`); no variants. Repeated
  `--pseudo "El=file.UPF"` builds the pseudopotential map (required),
  `--pseudo-dir` locates the UPF files, `--ecutwfc` (Ry, required) and
  `--ecutrho` set the cutoffs and `--kpts "k1 k2 k3"` the Monkhorst-Pack grid
  (omit for Γ-only). `--charge`/`--multiplicity` map to QE keywords for you:
  `--charge` → `tot_charge`, and any non-singlet `--multiplicity` turns on spin
  polarization (`nspin=2`, `tot_magnetization = multiplicity − 1`; a singlet
  stays `nspin=1`). Anything else is a repeated `--input "key=value"` (flat
  `pw.x` keywords ASE routes to the right section — `smearing`, `occupations`,
  …; an explicit `--input` keyword overrides the auto-set ones). `--command` is
  the `pw.x` executable (with any `mpiexec` prefix); `--command` and
  `--pseudo-dir` are given together or not at all. The force/stress flags are
  added automatically when a job needs them.

Omit `--variant` to take the calculator's default. `--precision` sets the
floating-point precision for MACE (`float32`/`float64`, default `float64`) and
Orb (`float32-highest` — the recommended default — `float32-high` or `float64`);
omit it to take the calculator's default. Each backend or variant is a small
template plus one registry entry, so biasing, the thermostat and the output
options work the same across all of them.

The same calculator flags carry over to the other job types. `sp run` writes a
single-point script (energy always; forces — and stress if periodic — unless
`--forces False`, which is cheaper). `relax run` writes a geometry optimization:
`--optimizer` picks `bfgs` (default), `lbfgs` or `fire`, `--fmax` sets the
force-convergence threshold (default `0.05` eV/Å) and `--nsteps` caps the
iterations; it relaxes atomic positions only (cell fixed) and writes the path to
`opt.traj`, the step log to `opt.log` and the final structure to `optimized.xyz`.

Command layout:

```
ase-cli-tools
├── md
│   └── run          -> generate an MD script; --job selects the ensemble
│                       (nvt, nve), --thermostat the NVT thermostat, --variant
│                       the calculator member, --plumed biasing
├── sp
│   └── run          -> generate a single-point energy & forces script for any
│                       calculator (ORCA is the natural QM backend)
├── relax
│   └── run          -> generate a geometry-optimization script; --optimizer
│                       picks BFGS/LBFGS/FIRE, --fmax the force threshold
└── postprocess
    └── wrap         -> generate a trajectory-wrapping script
```

Biasing is an **option on a job** (`--plumed`), not a job of its own — so it
applies uniformly to any ensemble as they are added.

## Roadmap

Calculators:

- [x] MACE
- [x] Orb
- [x] GRACE
- [x] ORCA
- [x] Quantum ESPRESSO
- [ ] CP2K
- [ ] VASP

Jobs / ensembles:

- [x] NVT thermostats: Nose-Hoover chain, Langevin, CSVR (Bussi)
- [x] NVE molecular dynamics
- [x] Single-point energy & forces (any calculator)
- [x] Geometry optimization (BFGS / LBFGS / FIRE; positions only)
- [ ] NPT molecular dynamics and barostats
- [ ] Frequency (vibrational) calculations
- [ ] Cell relaxation (positions + cell, via ASE Filters)
