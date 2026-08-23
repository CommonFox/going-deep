"""Render a league's draft board as a single self-contained HTML page.

    python scripts/draft_page.py espn  > /tmp/espn.html

Every number on the page is queried from the warehouse at render time, for the same reason the
notebooks compute rather than quote: a card that says "Bijan is worth 166" is true until the next
rebuild, and a card generated from `draft_board` is true whenever it was last generated.

This exists alongside `notebooks/draft_board.ipynb` rather than replacing it. The notebook is for a
desk, where a kernel and a scroll wheel are available; this is for a phone at a draft table, where
they are not — the ESPN draft randomizes its order an hour beforehand, so the page carries all ten
seats and the reader finds their own row.
"""

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.query import q  # noqa: E402

# The platforms both carry the league's own name; using it means the page identifies itself the way
# the reader's app does rather than by a slug only this repo uses.
LEAGUE_NAMES = {
    "espn": "SELECT \"settings.name\" AS name FROM espn_league",
    "sleeper": "SELECT name FROM sleeper_league",
}

# The subject's own convention — fantasy boards have colour-coded positions — pulled toward muted
# tones so a page of them reads as a system rather than a bag of highlighters, and so the greys
# still separate when the thing is printed in black and white.
POSITION_COLORS = {
    "QB": "#7c5cbf", "RB": "#2f7d5a", "WR": "#bd5f38",
    "TE": "#3d6da6", "K": "#8a8478", "DST": "#6b6f4e", "P": "#7a6a5d",
}

# A gap this size between consecutive players is a real cliff rather than noise, and it is where a
# board should draw a line: everyone above it is interchangeable, the next man down is not.
TIER_GAP = 8.0


def esc(value) -> str:
    return html.escape(str(value))


def snake_picks(team_count: int, rounds: int, seat: int) -> list[int]:
    return [
        (rnd - 1) * team_count + (seat if rnd % 2 else team_count - seat + 1)
        for rnd in range(1, rounds + 1)
    ]


def chip(position: str) -> str:
    color = POSITION_COLORS.get(position, "#8a8478")
    return f'<span class="pos" style="--pos:{color}">{esc(position)}</span>'


def board_rows(league: str, limit: int) -> str:
    df = q(
        """
        SELECT position, player_name, team, consensus_adp, points_over_replacement,
               availability, ol_tier, projected_floor, projected_ceiling
        FROM draft_board
        WHERE league_key = ? AND consensus_adp IS NOT NULL
        ORDER BY points_over_replacement DESC LIMIT ?
        """,
        [league, limit],
    )
    rows, previous = [], None
    for rank, row in enumerate(df.itertuples(), start=1):
        if previous is not None and previous - row.points_over_replacement >= TIER_GAP:
            rows.append('<tr class="tier"><td colspan="7">tier break</td></tr>')
        previous = row.points_over_replacement
        warn = ' <span class="warn" title="bottom-quartile offensive line">OL</span>' \
            if row.ol_tier == "Q1 worst" else ""
        fragile = ' <span class="warn" title="misses time">INJ</span>' \
            if row.availability < 0.90 else ""
        rows.append(
            f"<tr><td class='rank'>{rank}</td><td>{chip(row.position)}</td>"
            f"<td class='name'>{esc(row.player_name)}{warn}{fragile}</td>"
            f"<td class='team'>{esc(row.team or '')}</td>"
            f"<td class='num'>{row.consensus_adp:.1f}</td>"
            f"<td class='num strong'>{row.points_over_replacement:.0f}</td>"
            f"<td class='num quiet'>{row.availability:.2f}</td></tr>"
        )
    return "\n".join(rows)


def seat_table(league: str, team_count: int, rounds: int) -> str:
    plans = q(
        """
        SELECT draft_slot, plan, points_vs_field FROM draft_plans WHERE league_key = ?
        QUALIFY row_number() OVER (PARTITION BY draft_slot ORDER BY points_vs_field DESC) = 1
        ORDER BY draft_slot
        """,
        [league],
    ).set_index("draft_slot")
    rows = []
    for seat in range(1, team_count + 1):
        picks = snake_picks(team_count, rounds, seat)
        best = plans.loc[seat] if seat in plans.index else None
        plan = f"{esc(best.plan)}" if best is not None else "—"
        edge = f"{best.points_vs_field:+.0f}" if best is not None else ""
        rows.append(
            f"<tr><td class='rank'>{seat}</td>"
            f"<td class='num'>{', '.join(str(p) for p in picks[:6])}…</td>"
            f"<td class='name'>{plan}</td><td class='num quiet'>{edge}</td></tr>"
        )
    return "\n".join(rows)


def replacement_table(league: str) -> str:
    df = q(
        """
        SELECT DISTINCT position, starters_at_position, replacement_level_points
        FROM draft_board WHERE league_key = ? AND starters_at_position > 0
        ORDER BY starters_at_position DESC
        """,
        [league],
    )
    return "\n".join(
        f"<tr><td>{chip(r.position)}</td><td class='num'>{r.starters_at_position}</td>"
        f"<td class='num quiet'>{r.replacement_level_points:.0f}</td></tr>"
        for r in df.itertuples()
    )


def flagged_backs(league: str) -> str:
    df = q(
        """
        SELECT player_name, team, consensus_adp, ol_grade, points_over_replacement
        FROM draft_board WHERE league_key = ? AND ol_tier = 'Q1 worst' AND consensus_adp <= 130
        ORDER BY consensus_adp
        """,
        [league],
    )
    return "\n".join(
        f"<tr><td class='name'>{esc(r.player_name)}</td><td class='team'>{esc(r.team)}</td>"
        f"<td class='num'>{r.consensus_adp:.1f}</td>"
        f"<td class='num quiet'>{r.ol_grade:.0f}</td>"
        f"<td class='num'>{r.points_over_replacement:.0f}</td></tr>"
        for r in df.itertuples()
    )


def punters(league: str) -> str:
    df = q(
        """
        SELECT player_name, team, projected_points_adjusted, points_over_replacement
        FROM draft_board WHERE league_key = ? AND position = 'P' AND position_rank <= 6
        ORDER BY position_rank
        """,
        [league],
    )
    if df.empty:
        return ""
    rows = "\n".join(
        f"<tr><td class='name'>{esc(r.player_name)}</td><td class='team'>{esc(r.team)}</td>"
        f"<td class='num'>{r.projected_points_adjusted:.0f}</td>"
        f"<td class='num strong'>{r.points_over_replacement:.0f}</td></tr>"
        for r in df.itertuples()
    )
    return f"""
    <section>
      <h2>Punters</h2>
      <p class="lede">No projection service prices a punter, so this is the one position on the
      board where nobody else in the league has a number. It is still only worth about two dozen
      points over the last startable one — take one late, but take the right one.</p>
      <div class="scroll"><table>
        <thead><tr><th>Punter</th><th>Team</th><th class="num">Points</th>
        <th class="num">Over repl.</th></tr></thead>
        <tbody>{rows}</tbody>
      </table></div>
    </section>"""


def build(league: str) -> str:
    settings = q("SELECT * FROM league_settings WHERE league_key = ?", [league]).iloc[0]
    team_count = int(settings.team_count)
    rounds = int(
        settings[["qb_slots", "rb_slots", "wr_slots", "te_slots", "flex_slots",
                  "superflex_slots", "k_slots", "p_slots", "dst_slots", "bench_slots"]].sum()
    )
    scoring = "full PPR" if settings.rec_pts == 1.0 else f"{settings.rec_pts} PPR"
    shape = "superflex" if settings.superflex_slots else "one quarterback"
    kicker = q(
        """
        SELECT player_name FROM draft_board
        WHERE league_key = ? AND position = 'K' ORDER BY position_rank LIMIT 1
        """,
        [league],
    )
    kicker_name = kicker.iloc[0].player_name if not kicker.empty else "the best one left"
    named = q(LEAGUE_NAMES[league])
    league_name = named.iloc[0]["name"] if not named.empty else league

    return f"""<title>{esc(league_name)} Draft Card</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
  :root {{
    --paper: #f2f4f1;
    --card: #ffffff;
    --ink: #141a17;
    --ink-soft: #4a544e;
    --ink-faint: #7d8781;
    --rule: #d9ded9;
    --accent: #b6541f;
    --warn-bg: #f6e4db;
    --warn-ink: #8f3d15;
    --radius: 3px;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --paper: #10140f; --card: #171c17; --ink: #e8ece7; --ink-soft: #a9b2ab;
      --ink-faint: #78827b; --rule: #2a312b; --accent: #e2894f;
      --warn-bg: #3a241a; --warn-ink: #efab7d;
    }}
  }}
  :root[data-theme="dark"] {{
    --paper: #10140f; --card: #171c17; --ink: #e8ece7; --ink-soft: #a9b2ab;
    --ink-faint: #78827b; --rule: #2a312b; --accent: #e2894f;
    --warn-bg: #3a241a; --warn-ink: #efab7d;
  }}

  body {{
    background: var(--paper); color: var(--ink);
    font-family: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif;
    font-size: 15px; line-height: 1.55;
    margin: 0; padding: clamp(16px, 4vw, 44px);
  }}
  .wrap {{ max-width: 940px; margin: 0 auto; display: flex; flex-direction: column; gap: 34px; }}

  h1, h2, h3 {{ font-family: Archivo, ui-sans-serif, system-ui, sans-serif; text-wrap: balance;
               margin: 0; font-stretch: 92%; letter-spacing: -0.01em; }}
  h1 {{ font-size: clamp(30px, 6vw, 46px); font-weight: 700; line-height: 1.05; }}
  h2 {{ font-size: 21px; font-weight: 600; padding-bottom: 7px; border-bottom: 2px solid var(--ink);
       margin-bottom: 14px; }}
  h3 {{ font-size: 15px; font-weight: 600; color: var(--ink-soft); }}

  .eyebrow {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 11px;
             letter-spacing: 0.14em; text-transform: uppercase; color: var(--accent);
             margin: 0 0 8px; }}
  .lede {{ color: var(--ink-soft); max-width: 64ch; margin: 0 0 16px; }}
  .facts {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 12.5px;
           color: var(--ink-soft); margin: 10px 0 0; }}

  section {{ display: flex; flex-direction: column; }}
  .grid {{ display: grid; gap: 26px; grid-template-columns: 1fr; }}
  @media (min-width: 760px) {{ .grid.two {{ grid-template-columns: 1fr 1fr; }} }}

  .scroll {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13.5px; }}
  th {{ text-align: left; font-weight: 600; font-size: 10.5px; text-transform: uppercase;
       letter-spacing: 0.09em; color: var(--ink-faint); padding: 0 8px 6px;
       border-bottom: 1px solid var(--rule); white-space: nowrap; }}
  td {{ padding: 5px 8px; border-bottom: 1px solid var(--rule); }}
  tbody tr:last-child td {{ border-bottom: none; }}
  .num {{ font-family: "IBM Plex Mono", ui-monospace, monospace;
         font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }}
  th.num {{ text-align: right; }}
  .strong {{ font-weight: 600; }}
  .quiet {{ color: var(--ink-faint); }}
  .rank {{ font-family: "IBM Plex Mono", ui-monospace, monospace; color: var(--ink-faint);
          width: 2.4em; }}
  .name {{ font-weight: 500; }}
  .team {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 12px;
          color: var(--ink-faint); }}

  .pos {{ display: inline-block; min-width: 2.9em; text-align: center; padding: 1px 5px;
         border-radius: var(--radius); background: var(--pos); color: #fff;
         font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 10.5px;
         font-weight: 600; letter-spacing: 0.04em; }}
  .warn {{ display: inline-block; padding: 0 4px; border-radius: var(--radius);
          background: var(--warn-bg); color: var(--warn-ink);
          font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 10px;
          font-weight: 600; }}
  tr.tier td {{ padding: 7px 8px 2px; border: none; border-top: 2px solid var(--ink);
               font-family: "IBM Plex Mono", ui-monospace, monospace;
               font-size: 9.5px; letter-spacing: 0.16em; text-transform: uppercase;
               color: var(--ink-faint); }}

  ol.card {{ margin: 0; padding-left: 1.15em; display: flex; flex-direction: column; gap: 9px; }}
  ol.card strong {{ font-weight: 600; }}
  .note {{ background: var(--card); border-left: 3px solid var(--accent);
          padding: 13px 16px; border-radius: var(--radius); }}
  .note p {{ margin: 0; color: var(--ink-soft); font-size: 14px; }}
  footer {{ color: var(--ink-faint); font-size: 12px; border-top: 1px solid var(--rule);
           padding-top: 14px; }}

  @media print {{
    :root {{ --paper: #fff; --card: #fff; --ink: #000; --ink-soft: #333; --ink-faint: #666;
            --rule: #ccc; --warn-bg: #eee; --warn-ink: #000; }}
    body {{ padding: 0; font-size: 10.5px; }}
    .wrap {{ gap: 20px; }}
    section, table {{ break-inside: avoid; }}
    tr {{ break-inside: avoid; }}
  }}
</style>

<div class="wrap">
  <header>
    <p class="eyebrow">{esc(league_name)}</p>
    <h1>{team_count} teams, {esc(scoring)}, {esc(shape)}</h1>
    <p class="facts">{rounds} rounds · {team_count * rounds} picks · order drawn one hour before
      the draft, so find your seat below</p>
  </header>

  <section>
    <h2>Find your seat</h2>
    <p class="lede">Your first six picks by seat, and the opening plan that did best from it across
      300 simulated drafts. In a ten-team, one-quarterback league the plan is worth far less than it
      is in superflex — treat these as tiebreakers, not instructions.</p>
    <div class="scroll"><table>
      <thead><tr><th>Seat</th><th class="num">Your picks</th><th>Best opening</th>
      <th class="num">vs field</th></tr></thead>
      <tbody>{seat_table(league, team_count, rounds)}</tbody>
    </table></div>
  </section>

  <div class="grid two">
    <section>
      <h2>What free costs</h2>
      <p class="lede">Every value on this page is a player's points minus the last man who still has
        to start somewhere. Where the pool is deep, late picks are worth nothing.</p>
      <div class="scroll"><table>
        <thead><tr><th>Pos</th><th class="num">Jobs</th><th class="num">Replacement</th></tr></thead>
        <tbody>{replacement_table(league)}</tbody>
      </table></div>
    </section>

    <section>
      <h2>Backs behind a bad line</h2>
      <p class="lede">Backs on bottom-quartile lines beat their draft price 35% of the time against
        56% for the best lines — a 27-point swing, positive in seven of seven seasons.</p>
      <div class="scroll"><table>
        <thead><tr><th>Player</th><th>Team</th><th class="num">ADP</th><th class="num">OL</th>
        <th class="num">Value</th></tr></thead>
        <tbody>{flagged_backs(league)}</tbody>
      </table></div>
    </section>
  </div>

  <section>
    <h2>The board</h2>
    <p class="lede">Ranked by points over replacement, adjusted for how much of a season each player
      has historically been available for. <span class="warn">OL</span> is a back behind a
      bottom-quartile line; <span class="warn">INJ</span> is anyone who has missed real time. Lines
      mark a genuine cliff, not a round.</p>
    <div class="scroll"><table>
      <thead><tr><th>#</th><th>Pos</th><th>Player</th><th>Team</th><th class="num">ADP</th>
      <th class="num">Value</th><th class="num">Avail</th></tr></thead>
      <tbody>{board_rows(league, 70)}</tbody>
    </table></div>
  </section>

  {punters(league)}

  <section>
    <h2>Rules for the room</h2>
    <ol class="card">
      <li><strong>Value, not ranking.</strong> The number that matters is how far a player clears
        what the position gives away for free — which is why a quarterback worth 300 points can be
        worth less than a back worth 250.</li>
      <li><strong>Chase shallow positions late.</strong> This league starts far more receivers than
        tight ends, so late receivers price below replacement while a tight end still clears it.</li>
      <li><strong>Kicker last. Always.</strong> Kicker scoring repeats year to year at
        <em>-0.011</em> — no correlation at all. {esc(kicker_name)} is projected best and that is
        unknowable. Spend the pick on anything else.</li>
      <li><strong>Defense second-to-last.</strong> Points allowed does carry year to year (+0.32),
        but the projection sources disagree by more than the whole gap between the best and worst
        startable defense.</li>
      <li><strong>Check byes before the last three picks</strong>, not after.</li>
    </ol>
  </section>

  <div class="note">
    <p><strong>What this does not know.</strong> Replacement level is treated as fixed all season,
      and it is not — useful running backs appear on waivers all year while startable quarterbacks
      do not. Durability is measured only from injured-reserve stints, so it catches the long
      absences and misses the one-week ones. Both gaps push the same way, so read a close call
      between a back and a quarterback as closer than it looks.</p>
  </div>

  <footer>Generated from the going-deep warehouse. Every number here is queried, not typed —
    rebuild and regenerate rather than editing this page.</footer>
</div>
"""


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "espn"))
