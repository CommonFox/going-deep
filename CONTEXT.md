# going-deep

A self-hosted fantasy football warehouse: raw archives from every public projection and ADP source,
loaded into DuckDB, with derived models on top. This file fixes the vocabulary those models share,
because several of these words are used loosely in the wider fantasy world and one of them
(**format**) means two different things depending on which layer you are standing in.

## Language

### League shape

**Format**:
Which positions a league's starting lineup requires — specifically whether a second quarterback can
start. Only two exist here: `1qb` and `superflex`. A format is not a scoring rule; it changes who is
worth drafting without changing what anyone scores.
_Avoid_: 2QB (that is one source's name for the superflex board), lineup type, league type

**Scoring basis**:
What a reception is worth, and the rest of the points table with it — `standard`, `half_ppr`, `ppr`.
Orthogonal to format: a league is half-PPR *and* superflex at the same time.
_Avoid_: scoring format (the raw sources use this label for both axes, which is the confusion this
pair of terms exists to prevent)

**Variant**:
`draft_strategy`'s word for a format when it is being run as a counterfactual — a league's real
format and the other one it could have had. Same two values as **format**, and it joins to them.
_Avoid_: scenario, mode

**Seat**:
Which position in the draft order a team picks from, 1 through `team_count`. In a snake draft the
seat determines the entire shape of a team's picks: seat 1 gets one pick at the top and then
back-to-back pairs, the middle seats get an even drip.
_Avoid_: draft position (that means where a *player* goes), pick number

### Value

**Replacement level**:
The points a freely available player at a position is expected to score — concretely, the last one
who still has to start somewhere in the league once every team's slots are filled. The zero point
against which a drafted player's value is measured.
_Avoid_: baseline, waiver level, the next guy at my next pick (that player is not free; taking him
costs a pick)

**Points over replacement**:
A player's points minus his position's replacement level. The only unit in which positions are
comparable, and the reason superflex changes a draft: it moves quarterback replacement level, not
quarterback scoring.
_Avoid_: VORP, value, surplus (that word is taken — see **Surplus**)

**Surplus**:
What a player returned relative to what his draft price implied he should. A judgement about the
*market*, where **points over replacement** is a judgement about the *player*.
_Avoid_: value, edge

**Availability**:
The share of a season a player is expected to be able to play, measured from his own history of
being on injured reserve. Distinct from **role**: availability is whether he *can* play, role is
whether he *would* be played.
_Avoid_: durability, health, expected games (that is one model's internal estimate, which conflates
availability with role), and never for draft supply — that is **survival probability**, which is a
fact about a draft room rather than about a player

**Role**:
How much a player would be used if fit — starter, committee back, backup. Already priced into every
external projection through its per-game term, which is why it must not be counted again as
availability.
_Avoid_: usage, opportunity, snap share

### Drafting

**Board**:
Every draftable player for one league, priced in that league's scoring basis and roster slots. What
you read on draft night.
_Avoid_: rankings, cheat sheet, big board

**Draftable**:
Priced by the market (has a **consensus ADP**) *or* ranked inside the league's starter depth at
the position. The second arm is not redundant: a rookie can have no ADP at all and still be the
5th kicker on a board where 14 get rostered, and an ADP-only rule would drop him. Below this cut
sit third-string quarterbacks and camp-body kickers, whom a build counts rather than names.
_Avoid_: rosterable, relevant, in range

**ADP**:
Average draft position — the mean pick at which a player actually went, across many real drafts in
a stated format. A market price, never a projection or an opinion.
_Avoid_: rank, consensus rank

**Plan**:
A claim about roster construction rather than about a player: which positions to take in the first
five rounds. Scored on the whole roster it produces, not on those five picks.
_Avoid_: strategy (too broad — a plan is specifically an opening)

**Composition**:
A **plan** stated as counts without an order — "two quarterbacks and three running backs". The unit
most plan questions are actually asked in, since ADP resolves the ordering the way a human would.
_Avoid_: build, roster construction

**Ordering**:
A **plan** stated as a specific sequence — quarterback first, then two backs. Only worth
distinguishing from **composition** where the sequence itself is the claim being tested.
_Avoid_: draft order (that means **seat** ordering, not position ordering)

**Field**:
The other teams in a simulated draft, and how they behave. A field drafting straight off ADP is the
friendliest possible room, because the focal team is the only one deviating.
_Avoid_: opponents, league mates

**Survival probability**:
How likely a player is to still be undrafted at a given pick. A fact about supply in a draft room,
and nothing to do with **availability**, which is how much of a season he can play — the word is
already spent, and the collision is why this term exists. `draft_availability` predates the
distinction and still carries the older name; nothing a drafter reads does.
_Avoid_: availability, p_available, still on the board (that is an observation, not a probability)

**Cost of waiting**:
The points over replacement expected to be lost by passing on a player at one turn and hoping he
lasts to the seat's next one. Combines his **survival probability** with the drop-off to whoever
would be left at his position, so a deep position is cheap to wait on even when the individual
player is likely to go, and a cliff is expensive even when he will probably last. Zero at the
turn, where the two picks are adjacent and there is no gap to survive.
_Avoid_: urgency, opportunity cost, need (that is a roster question, not a supply one)
