# Real APBS electrostatics demo.
# Inputs are produced by scripts/apbs/prepare_apbs.sh (pdb2pqr + apbs):
#   scripts/apbs/2ewn.pqr  per-atom charges/radii
#   scripts/apbs/2ewn.dx   Poisson-Boltzmann potential map (kT/e)

# load the APBS-derived structure and potential map
load scripts/apbs/2ewn.pqr, 2ewn
load scripts/apbs/2ewn.dx, 2ewn_potential

# molecular surface coloured by electrostatic potential (-5 .. +5 kT/e)
show surface, 2ewn
ramp_new 2ewn_esp, 2ewn_potential, [-5, 0, 5], [red, white, blue]
set surface_color, 2ewn_esp, 2ewn

# set the view
orient 2ewn

# render & save
ray 1920, 1080
png assets/demo_apbs.png
quit
