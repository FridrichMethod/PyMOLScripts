# fetch and preprocess the structure
fetch 1ec6, async=0
remove sol.
remove chain B + chain C

# set the color scheme
color grey80, chain A
set cartoon_transparency, 0.5, chain A
spectrum b, blue_white_red, chain D

# set the view
orient

# render & save
ray 1920, 1080
png assets/demo_na.png
quit
