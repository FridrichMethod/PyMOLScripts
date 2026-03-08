# fetch and preprocess the structure
fetch 6hyj, async=0
remove sol.
remove chain B

# set the color scheme
spectrum b, rainbow2, pol.
util.cba(144, "resn SEP", _self=cmd)

# set the view
show spheres, resn SEP
orient

# render & save
ray 1920, 1080
png assets/demo_cartoon.png
quit
