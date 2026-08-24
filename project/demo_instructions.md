ok, for now let's just build a sample dashboard, complete with a sample of actual players (use data/sample.csv, assume top 15 market value attackers as results of AI algorithm). I want something that has 2 versions:


# Version 1


An Interactive Version:
 1. filterable/order by age, market value, number of recommended players, etc. Must be professional, easy to navigate and look like a complete product. Ill give you some data after

Now, in order, the must haves:

 1. must have the top 3 podium first (idenitfy each player correctly), then a properly formatted list of the remaining N-3 best recommendations
 2. must have a section that compares ALL of the recommended players using all of the plots inside project/imgs_demo except comparison.png
 3. must have a section that compares players on demand from the recommended list, side by side, choosing up to 5 players to compare in that hexagon image (project/imgs_demo/comparison.png). each player must be selecteable through a search that shows options based on whats already written (if written string inside any name/id then show it as an option to click). the default viewing position for this section must be comparing the top 3 players across the deemed important metrics (reminder below).

## Important Metrics (focusing attackers)


# Version 2 

The Downloadable Report:

 1. Outputs both as structured pdf, each section properly organized in a new page if needed:
    1. report_filtered: current state of all plots 
    2. report_original: the default state 


