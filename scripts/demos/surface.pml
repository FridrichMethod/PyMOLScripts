# fetch and preprocess the structure
fetch 2ewn, async=0
remove chain B
remove sol.

# set the color scheme
color_deep lightblue, resi 1-61
color_deep palegreen, resi 62-270
color_deep lightpink, resi 271-320
util.cba(144,"resn BTX",_self=cmd)

# set the view
show surface, pol.
orient

# render & save
ray 1920, 1080
png assets/demo_surface.png
quit
