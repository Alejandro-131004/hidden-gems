# 1. Underrepresentation Issue

Global features should suffice for most cases, but obviously we must separate players who are not usually expected to score goals, which will otherwise be buried under Attacker > Goalkeeper which is counterintuitive.

In other (Claude) words:

 - **Goalkeepers — severely underrepresented.** *None* of the global features apply.
  Wyscout top-up: `Save rate, %`, `Prevented goals per 90`, `Conceded goals per 90`, `Exits per 90`, `Accurate passes, %`.
 - **Centre-backs — underrepresented.** Their craft is invisible to the global list.
  Wyscout top-up: `Defensive duels won, %`, `Aerial duels won, %`, `PAdj Interceptions`, `Shots blocked per 90`, `Accurate progressive passes, %`.
 - **Defensive / central midfielders — partially underrepresented.** Global passing
  features cover circulation, but not the screen.
  Wyscout top-up: `PAdj Interceptions`, `Successful defensive actions per 90`, `Accurate forward passes, %`, `Progressive passes per 90`.
 - **Full-backs — partially covered.** The attacking half (crosses, `xA`) is captured; the
  defensive half is not. Wyscout top-up: `Defensive duels won, %`, `PAdj Interceptions`, `Crosses per 90`, `Accurate crosses, %`.

Attacking midfielders, wingers and strikers are **well represented** by the global list — they are precisely the profiles both studies reward — so they need no defensive top-up, only the within-role percentiling that keeps a striker from being scored against a centre-back's tackle count.

## More Detailed Explanation

### Flagged: Cited as Predictive but Unavailable in Wyscout
 1. **Reactions** (AMSTAT)
    1. Wyscout: no event equivalent: *flagged, no reliable proxy*
 2. **Composure** (AMSTAT)
    1. Wyscout: no event equivalent: *flagged; weak proxy* `Goal conversion, %`
 3. **Man of the Match** (Yalçınkaya, 0.42)
    1. Wyscout: an award, not a metric: *flagged, no proxy*
 4. **Dispossessed / Unsuccessful touches** (Yalçınkaya)
    1. Wyscout: not exported: *flagged, no proxy*
 5. **League strength** (Yalçınkaya: only `League_England` mattered)
    1. Wyscout: `League`: *not applicable; our targets are lower tiers, so league is a value ceiling, not a driver*

### Role-Specific Features
The studies value attacking output and report defensive actions as *negatively* correlated with value: a position confound. The role-independent list therefore **underrepresents** the groups below, which are ranked on their own craft (percentiled within the role).

#### Goalkeeper: *severely underrepresented (no role-independent feature applies)*
 1. **Shot-stopping**
    1. Wyscout: `Prevented goals per 90`, `Save rate, %`
 2. **Goals conceded in context**
    1. Wyscout: `Conceded goals per 90`, `xG against per 90`
 3. **Command of the area**
    1. Wyscout: `Exits per 90`
 4. **Distribution**
    1. Wyscout: `Accurate passes, %`, `Accurate long passes, %`

#### Centre-back: *underrepresented*
 1. **Aerial dominance**
    1. Wyscout: `Aerial duels won, %`
 2. **Duel defending**
    1. Wyscout: `Defensive duels won, %`
 3. **Reading / interceptions**
    1. Wyscout: `PAdj Interceptions`
 4. **Blocking**
    1. Wyscout: `Shots blocked per 90`
 5. **Ball progression from the back**
    1. Wyscout: `Progressive passes per 90`, `Accurate progressive passes, %`

#### Full-back / Wing-back: *partially covered (attacking half via shared list, defensive half missing)*
 1. **Duel defending**
    1. Wyscout: `Defensive duels won, %`
 2. **Reading / interceptions**
    1. Wyscout: `PAdj Interceptions`
 3. **Crossing**
    1. Wyscout: `Crosses per 90`, `Accurate crosses, %`
 4. **Overlap / carrying**
    1. Wyscout: `Progressive runs per 90`, `Accelerations per 90`

#### Defensive / Central Midfielder: *partially underrepresented (the screen is missing)*
 1. **Ball-winning screen**
    1. Wyscout: `PAdj Interceptions`, `Defensive duels won, %`, `Successful defensive actions per 90`
 2. **Progressive distribution**
    1. Wyscout: `Progressive passes per 90`, `Accurate forward passes, %`
 3. **Availability to receive**
    1. Wyscout: `Received passes per 90`

#### Attacking Midfielder / Winger / Striker: *well represented by the role-independent list*
 1. **Box presence** *(striker / advanced roles)*
    1. Wyscout: `Touches in box per 90`
 2. **Aerial threat** *(striker)*
    1. Wyscout: `Head goals per 90`, `Aerial duels won, %`
 3. **1v1 acceleration** *(winger)*
    1. Wyscout: `Accelerations per 90`