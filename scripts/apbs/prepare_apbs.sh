#!/usr/bin/env bash
#
# Generate the APBS electrostatic potential map for the APBS surface demo.
#
# Pipeline (real Poisson-Boltzmann electrostatics):
#   1. download the PDB entry
#   2. isolate a single protein chain
#   3. pdb2pqr -> per-atom charges/radii (.pqr) + a template APBS input (.in)
#   4. apbs    -> electrostatic potential map (.dx, units kT/e)
#
# Requires `pdb2pqr30` (or `pdb2pqr`) and `apbs` on PATH.
# Outputs <id>.pqr and <id>.dx next to this script; scripts/demos/apbs.pml
# loads those two files to render assets/demo_apbs.png.
#
# Usage: bash scripts/apbs/prepare_apbs.sh [PDB_ID] [CHAIN]
set -euo pipefail

PDB_ID="${1:-2ewn}"
CHAIN="${2:-A}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

pdb2pqr_bin="$(command -v pdb2pqr30 || command -v pdb2pqr)"

echo "[apbs-prep] downloading ${PDB_ID}..."
curl -sSL -o "${PDB_ID}.pdb" "https://files.rcsb.org/download/${PDB_ID^^}.pdb"

echo "[apbs-prep] isolating chain ${CHAIN} (protein atoms only)..."
awk -v ch="$CHAIN" 'substr($0,1,4)=="ATOM" && substr($0,22,1)==ch' \
    "${PDB_ID}.pdb" > "${PDB_ID}_chain.pdb"

echo "[apbs-prep] assigning charges/radii with pdb2pqr (AMBER force field)..."
"$pdb2pqr_bin" --ff=AMBER --whitespace --drop-water \
    --apbs-input "${PDB_ID}.in" "${PDB_ID}_chain.pdb" "${PDB_ID}.pqr"

echo "[apbs-prep] configuring APBS input (write potential map, 0.150 M NaCl)..."
# Name the output map after the stem (-> <id>.dx) and add physiological salt.
sed -i "s#write pot dx ${PDB_ID}.pqr#write pot dx ${PDB_ID}#" "${PDB_ID}.in"
sed -i "s#^    lpbe#    ion charge +1 conc 0.150 radius 2.0\n    ion charge -1 conc 0.150 radius 2.0\n    lpbe#" \
    "${PDB_ID}.in"

echo "[apbs-prep] running APBS (linearised Poisson-Boltzmann)..."
apbs "${PDB_ID}.in"

# APBS appends a -PE0 processor tag for mg-auto runs; normalise the name.
if [[ -f "${PDB_ID}-PE0.dx" ]]; then
    mv -f "${PDB_ID}-PE0.dx" "${PDB_ID}.dx"
fi

echo "[apbs-prep] done -> ${HERE}/${PDB_ID}.pqr , ${HERE}/${PDB_ID}.dx"
