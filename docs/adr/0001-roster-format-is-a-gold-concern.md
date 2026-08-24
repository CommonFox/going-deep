# Roster format is separated from scoring format in gold, not silver

FantasyFootballCalculator serves its superflex board from the same endpoint as its scoring formats,
so `2qb` arrives labelled as a `scoring_format` alongside `standard`, `half-ppr` and `ppr`. It isn't
one — nothing about how points are awarded changes, only how many quarterbacks a lineup can start —
but silver mirrors its sources exactly, so `ffc_adp.scoring_format` keeps the source's label and the
two axes are separated in gold instead: `adp_consensus` carries a `format` dimension (`1qb` /
`superflex`) that `draft_strategy.variant` and `draft_board` join against, while scoring lives on
`consensus_projections.scoring`.

## Considered options

Adding a `roster_format` column in silver was the obvious alternative, and it was rejected because
it makes silver assert something the source did not say. The load step would have to *know* that
`2qb` is a lineup rule, which is exactly the kind of interpretation that makes a raw archive stop
being replayable — the archive should be able to rebuild the warehouse without carrying anyone's
opinions about what the source meant.

## Consequences

`adp_consensus` has two rows per player-season where it previously had one, so every consumer must
filter on `format`. All six existing ones are pinned to `'1qb'`, which preserves their prior
behaviour exactly; a model that forgets the filter silently doubles its rows rather than failing,
which is the sharp edge here.

The upside is worth it: a superflex league priced off a 1QB board is not slightly wrong, it is
reading another league's price sheet. The same Josh Allen is ADP 1.7 on one and outside the top 60
on the other, and `draft_strategy` had already documented reading the wrong one as its single
biggest known limitation.
