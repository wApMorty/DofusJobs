Build a web application that computes optimized gathering-job (metiers de recolte) leveling routes for Dofus (modern Unity edition, 2.x/3.x).

PROBLEM
The user inputs their current XP/level in each gathering job (Bucheron/Lumberjack, Paysan/Farmer, Alchimiste/Herbalist, Mineur/Miner, Pecheur/Fisherman) and a maximum pods (inventory weight) capacity. The app returns an optimized farming route for a single pass that maximizes total job XP gained while respecting the pods limit and minimizing wasted travel.

This is a prize-collecting / orienteering optimization with capacity (pods) and job-level gating:
- A resource can only be harvested if the player's job level meets its required_level (eligibility gating). This is the ONLY way player level affects the result.
- XP gained per harvest is the resource's FIXED per-resource XP value (base_xp), exactly as in real Dofus gathering: harvesting a given resource always yields the same XP regardless of how far above the resource the player is. There is NO over-level diminishing-returns multiplier for gathering jobs (that only applies to crafting). Do NOT scale XP by player level; level only gates which resources are eligible.
- Each item has a pods (weight) cost; the carried total cannot exceed the pods limit.
- The route incurs real travel costs; the objective explicitly trades XP against travel (see OBJECTIVE).

OBJECTIVE (weighted trade-off, NOT lexicographic)
Maximize a weighted score: score = total_xp - lambda * total_travel_cost, where total_travel_cost is the sum of A* shortest-path step costs (1 unit = 1 map-screen transition) between consecutive visited spots, and lambda >= 0 is a configurable travel-penalty weight (XP-per-screen). A detour is only worth taking when its extra XP outweighs lambda times its extra travel. The web form exposes lambda as a tunable input with a sensible documented default; ties broken by least total_travel_cost then ascending spot_id for deterministic output. The route must genuinely avoid low-value detours — do not send the player across the map for a marginal XP gain.

DATA (scrape online)
Scrape game data from an online source such as DofusDB.fr / Dofapi: resource spots with map coordinates, harvest XP by level, item pods weights, job-level requirements per resource. Build a robust ingestion layer with local caching so the optimizer does not hit the network on every run. Respect rate limits.

TRAVEL MODEL (full map graph)
Each spot is a node; travel costs are derived from real map coordinates / world-map adjacency. Pathfinding over the graph to compute realistic move costs between spots.

DELIVERABLE
A web app:
- Input form: current XP (or level) per gathering job + pods limit.
- Output: the optimized ordered route (sequence of spots), with expected total XP gain (and per-job breakdown), pods used, and the harvest actions to perform at each stop.
Include: the optimization engine, the data ingestion/caching layer, automated tests, and clear run instructions.
