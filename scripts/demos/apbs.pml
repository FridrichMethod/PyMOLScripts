# fetch and preprocess the structure
fetch 2ewn, async=0
remove chain B
remove sol.

# generate the vacuum electrostatic potential surface (APBS-style coloring)
util.protein_vacuum_esp("2ewn", mode=2, quiet=0, _self=cmd)

# set the view
orient

# render & save
ray 1920, 1080
png assets/demo_apbs.png
quit
