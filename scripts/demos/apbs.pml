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

# set the view (same camera as scripts/demos/surface.pml)
set_view (\
    -0.278456718,    0.878569365,    0.388043523,\
    -0.570280433,    0.173850223,   -0.802842677,\
    -0.772814512,   -0.444850624,    0.452621192,\
    -0.000000000,    0.000000000, -208.479782104,\
     3.418830872,   47.872474670,  -38.216609955,\
   164.367050171,  252.592514038,   20.000000000 )

# render & save
ray 1920, 1080
png assets/demo_apbs.png
quit
