"""ase-cli-tools: a Typer-based front-end for building and running ASE jobs.

The package is deliberately split so the *interface* (Typer commands in
``app.py``) is separate from the *work* (``core.py``), with typed job
descriptions in between (``config.py``). New job types should add a config
dataclass, a core function, and a thin Typer command.
"""

__version__ = "0.1.0"
