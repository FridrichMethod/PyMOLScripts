# fetch and preprocess the structure
fetch 3a7r, async=0
remove sol.
remove resn SO4

# set the color scheme
color grey80, pol.
set cartoon_transparency, 0.8
util.cba(11, "resn LAQ", _self=cmd)
util.cba(5274, "br. resn LAQ a. 3.5", _self=cmd)

# set the view
show sticks, br. resn LAQ a. 3.5
orient
zoom (br. resn LAQ a. 3.5) + resn LAQ

# render & save
ray 1920, 1080
png assets/demo_sticks.png
quit
