#!/usr/bin/env python3
"""
Wrap every frame of an MD trajectory back into the primary cell.

Reads a trajectory (any ASE-readable format: .traj, .extxyz, ...), applies
`atoms.wrap()` to each frame using that frame's cell and periodic boundary
conditions, and writes the result to a new file (the input is left untouched).

Examples:
    python wrap_traj.py equil.traj
    python wrap_traj.py equil.traj -o equil_wrapped.extxyz
"""

import argparse
import os
import sys

from ase.io import read, write


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Wrap all frames of a trajectory into the primary cell.")
    p.add_argument("input", help="Input trajectory (.traj, .extxyz, ...).")
    p.add_argument("-o", "--output", default=None,
                   help="Output file. Default: '<input>_wrapped.extxyz'.")
    args = p.parse_args(argv)

    if not os.path.isfile(args.input):
        raise SystemExit(f"Input trajectory not found: {args.input}")

    out = args.output
    if out is None:
        root, _ = os.path.splitext(args.input)
        out = f"{root}_wrapped.extxyz"

    frames = read(args.input, index=":")     # read every frame
    for atoms in frames:
        atoms.wrap()                         # uses each frame's cell + pbc

    write(out, frames)
    print(f"Wrapped {len(frames)} frames: {args.input} -> {out}")


if __name__ == "__main__":
    sys.exit(main())
