# Late positions are held at display time, not repriced

`draft_board` prices a kicker correctly. There is one K slot, fourteen of them get rostered in a
fourteen-team league, and the drop from the best kicker to the fifteenth is arithmetic — Brandon
Aubrey comes back at 22.0 points over replacement and the market takes him at ADP 127.3. Cost of
waiting then multiplies a real drop by a real hazard, and in round 7 of the first live mock it
produced a defense near the top of the board with nothing wrong in the sum.

What neither number can encode is that the *position's* supply outlasts the draft. `waiting` prices
the drop behind one player weighted by that player's chance of being taken; for a **late position**
the answer is always that someone almost as good is still sitting there ten rounds later, and the
sentence a drafter needs — "this kicker will be here in round 14, this receiver will not" — is not a
fact about either player.

`src/draft/hold.py` therefore removes the question from the screen until the round it has an answer
worth having: one round held in reserve for each late slot the league starts, counted back from the
end. It reads `league["slots"]`, changes no number, and is pure in the same way the rest of
`src/draft/` is. Every value on a held position is exactly the value the warehouse rebuild computed
for it.

`draft_plans` was checked first, since it is the only thing in the repo that has simulated a draft
and could have settled the round empirically. It cannot: every plan it holds is a five-pick opening
of QB/RB/WR/TE, and the simulation never takes a kicker at all.

## Considered options

**A positional scarcity term in the pricing**, which is the honest fix and was the ticket's own
closing suggestion. `points_over_replacement` is measured against a freely available player, which
is a season-long fact and is not what makes a kicker different. What makes him different is the
*shape of the tail behind* replacement level — flat across dozens of players, where the tail behind
a startable back falls away. A term that knew that would price every position correctly and this
module would not exist.

Rejected on three counts, and the first is the weakest of them:

- It is warehouse work. A scarcity term belongs in `src/gold`, changes `draft_board` for both
  leagues, and has to be defended against every consumer of points over replacement —
  `draft_value`, `draft_strategy`, the notebooks that quote it. The hold is one small module under
  `src/draft/`, reads nothing and writes nothing, and can be deleted without a rebuild. That is a
  cost argument rather than a correctness one, and on its own it would not be enough.
- The benefit is confined to two positions with one starting slot each, in the last two rounds of a
  fifteen-round draft. No other position's ordering would move, because no other position's tail is
  flat — so the term would be carried by the whole warehouse to change the bottom of one screen.
- The honest version is harder than it looks. "Flat and deep" is not a property of a position, it is
  a property of a position *within a league's slot structure*: the same kicker pool is deep behind
  one K slot and thin behind three. A pricing term would need the same league shape the hold reads
  directly, one layer further from where that shape is known.

**A soft de-rank rather than a filter.** Keeps a kicker on screen and pushes him down by a penalty,
which is the more forgiving behaviour and was the alternative the ticket named. Rejected because the
penalty is a number nobody can audit under a pick clock, and this repo's standing preference — see
the individually visible source columns on every consensus model — is to keep a metric's inputs
apart rather than fold them into one score. A filter is readable in a way a penalty is not: he is
not there, and the screen says why.

**A hardcoded "last two rounds".** The right answer for this league and the wrong shape of answer.
Fifteen rounds with one K slot and one DST slot leaves exactly two picks that must be spent on them,
so the constant is true here and quietly stops being true if either number changes. Deriving the
reserve from the slots gives the identical round 14 and survives a settings change.

## Consequences

The board's numbers are untouched, which keeps the promise the live tool is built on: every value on
screen came from a rebuild that can be re-run and read, and none of it is computed on the night.
`hold` changes only *when a position is offered*, never what it is worth.

Because it is a rule about when to ask rather than about value, it has to be visible or a board
missing two positions reads as a board that has lost them — the same failure the hand-marked block
was written for. So the note names what is held, when it returns, and how to see it anyway.

That last part makes the position filter load-bearing. Typing `k` is now the only route to a held
position, so a change that stopped `filter.read_position` recognising a position token would turn
the hold from a default into a wall, silently. `test_a_typed_position_narrows_the_board_to_it` and
`test_asking_for_kickers_by_name_shows_them_whatever_round_it_is` are what hold the two ends of that
together, and they are worth more than they look.

The rule is applied by one function to two frames — the candidate list and the depth block — because
a position held out of one and reported by the other leaves the screen arguing with itself. Nothing
enforces that a third block making claims about positions would call `withhold` too, and that is the
sharp edge here.

The pricing fix stays open, and this is the decision to revisit rather than build around. What would
reopen it: a league whose late positions carry real spread, a scoring basis that makes kickers a
genuine differentiator, or a scarcity term wanted for other reasons anyway. In any of those cases the
hold becomes redundant and should be deleted, not kept running alongside a pricing term that already
knows what it knows.
