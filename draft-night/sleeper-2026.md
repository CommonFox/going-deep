# Sleeper draft sheet — 2026

**League:** Sleeper, 14-team, **superflex**, half-PPR, 15 roster spots
(QB / RB×2 / WR×2 / TE / FLEX / SUPERFLEX / K / DST / 5 bench).
**Seat: 1.01.** Picks: **1**, then pairs at **28–29**, **56–57**, **84–85**, **112–113**,
**140–141**, **168–169**, **196–197**.

All numbers are from the warehouse build (days-old, not recomputed live): `draft_plans` (this seat
simulated 300×/opening), `draft_availability` (survival to each of this seat's picks), `draft_board`
(points over replacement = **POR**). Board ADP is FFC's superflex board.

---

## THE SHEET

**Target composition (rounds 1–5): 2 QB, 2 RB, 1 TE.** +114.5 pts/season vs field, 2.7 avg finish,
33% win, 85% top-third. Every 2-QB opening beats every 1-QB opening by 5+ stderr.

| Pick | Round | Do this | Why / fallback |
|---|---|---|---|
| **1** | 1 | **RB — Gibbs** (or Bijan) | Gibbs 182 POR vs Bijan 172 vs Allen 140. Not Allen — QB value comes later. |
| **28, 29** | 2–3 | **QB + QB** — two of {Bo Nix, Jaxson Dart, or a falling Goff/Herbert/Mahomes} | This is your last shot at a starting QB2. Wait and it's Jordan Love (~66%) then streamers. **Ignore the WRs here** (Jefferson/Pickens/AJB) — their POR is below both the QBs and the RB alternative. |
| | | *Deviate only if* Saquon/Achane fell, or Chase Brown is there AND QBs look safe to 56 → RB at 28, QB+QB at 29 & 56. | |
| **56, 57** | 4–5 | **RB + TE** | TE: **McBride** (~77% here) else **Bowers** (~63%). RB: Etienne/Swift (~66 POR) or the floor pick **Bucky Irving** (~91%, and the +15 archetype). |
| | | *If McBride AND Bowers both gone* (~25%) → take RB + WR here, stream TE with Tyler Warren at pick 84 (~76%). | |
| **84, 85** | 6–7 | Best RB/WR upside; **Tyler Warren** if TE still open | Last picks with meaningfully positive POR. RB: Rhamondre / Jaylen Warren (~28). WR: DK Metcalf / Sutton (~22). |
| **112, 113** | 8–9 | RB/WR flier; **Kelce or Kittle** (POR 25–32, ~80–94%) if you never got a TE | Safety net for the TE punt. |
| **140–169** | 10–13 | Pure upside darts — **handcuff your own RBs** (a Gibbs injury makes Montgomery an RB1), swing on a rookie WR | Board POR is negative for everything realistically available by round 12. No gems here. |
| **196, 197** | 14–15 | **K + DST** (either order) | 1 slot each, 14 rostered, K1→K15 drop is small and preseason-random. Every K/DST is still there. The live tool hides both until round 14 — type `k` / `dst` to override. |

**Two hard rules:**
- **Compare POR, take the bigger number.** Only take a WR over an RB when its POR is genuinely
  higher — which, at your seat, doesn't happen before round 5.
- **Fade Unproven Veteran WRs** (never-elite, aged: Christian Watson, Jakobi Meyers, Michael
  Pittman). −3.8 pts under ADP, the one reliable negative signal.

---

## Convo A — the opening plan

### Composition, ranked (`draft_plans`, seat 1.01)

| Opening (first 5 rounds) | Pts vs field | Avg finish (/14) | Win rate | Top-third |
|---|---|---|---|---|
| **2QB 2RB 1TE** | **+114.5** ±3.2 | **2.7** | 33% | 85% |
| 2QB 2RB 1WR | +107.9 | 2.9 | 30% | 81% |
| 2QB 1RB 1WR 1TE | +107.2 | 3.0 | 30% | 82% |
| 2QB 3RB | +96.6 | 3.4 | 25% | 76% |
| best 1-QB plan (1QB 2RB 1WR 1TE) | +92.0 | 3.8 | 29% | 69% |
| 1QB 1RB **3WR** | +39.1 | 5.9 | 12% | 39% |
| 5WR | −173.6 | 12.7 | 0% | 0% |

- **2 QBs by end of round 3.** The QB well runs dry fast in a 14-team superflex; the 2-QB edge is
  5+ standard errors.
- **WR-heavy openings finish ~6th.** Your instinct to fade the tempting WR is correct.
- **TE vs WR for the 5th piece is ~a coin flip** — TE is preferred only because McBride/Bowers are
  likely to fall to pick 56.

### The QB cliff (why the timing is forced)

- Likely at picks 28–29: Bo Nix (POR 70, ~65%), Jaxson Dart (POR 66, ~70%), + ~25–35% each that
  Goff / Herbert / Mahomes (POR 56–66) slides.
- At pick 56: **Jordan Love ~66%**, Shough ~30%, then nothing viable.
- At pick 84: Malik Willis / Bryce Young / streamers.

Splitting the QBs (one at 28, one at 56) gambles on Jordan Love at 66%. If you want them separate,
still take both across 28 & 29.

### Round 1: Gibbs vs Bijan

Gibbs 181.7 POR / 95% available, Bijan 172.3 / 97%. Board says Gibbs; Bijan a fine swap on
preference. Josh Allen (POR 140) is the wrong pick — 2-QB means QB value is available for 10 more
rounds; another Gibbs is not.

---

## Convo B — archetypes & tiebreakers

### Two hard signals, the rest is noise

`archetype_outcomes`, 8 seasons, sleeper. Only two cells clear the error bars:

| Archetype | Edge vs ADP | |
|---|---|---|
| **Proven Prime RB** (young/prime **and** a prior elite finish) | **+15 pts** (t = 2.4) | target — 52% elite rate, 5% bust |
| **Unproven Veteran WR** (journeyman, never elite, 27+/7yr+) | **−3.8 pts** (t = −3.5) | fade |

Everything else (veteran-RB decline, breakout-youth bonus, proven-prime WR) has a surplus
indistinguishable from zero. Color, not a tiebreaker.

- **Proven Prime RBs in range:** Gibbs, Bijan, Achane, Jeanty, Chase Brown, Breece Hall, Kyren
  Williams, **Bucky Irving**, Javonte Williams.
- **Unproven Veteran WRs to fade (late depth):** Christian Watson, Jakobi Meyers, Michael Pittman.
  Prefer an Unproven Youth WR (Carnell Tate, Rome Odunze, Marvin Harrison Jr., Brian Thomas Jr.).

### Color worth knowing

- **QBs never bust — 0% bust rate across every QB archetype.** They get hurt (~20%) or deliver.
  This is *why* the 2-QB plan is safe, not just high-EV.
- **Proven Veteran RBs** (CMC 30, Henry 32, Saquon 29): age doesn't dent surplus (≈fair value) but
  ~35% have a season where something breaks vs ~26% for Proven Prime.
- **Proven Prime WRs** (Chase, Puka, JSN, London, Pickens): 14% bust rate, no value edge.

### Tiebreakers

1. **Round 4–5 RB → Bucky Irving.** Proven Prime RB + ~91% at pick 56. Etienne (Proven Veteran) /
   Swift (Unproven Veteran) carry ~12 more POR — take them only for ceiling.
2. **TE → McBride if there, else Bowers.** Board favors McBride by 14 POR; archetype nudges Bowers
   (younger), not enough to override.
3. **QB2 (Nix / Dart)** — no flag, low-variance position.

### Cross-position tiers (POR)

| | RB | WR | QB | TE |
|---|---|---|---|---|
| **T1** | Gibbs 182, Bijan 172 | Chase 134, Puka 132 | **Allen 140** | McBride 77, Bowers 63 |
| **T2** | JT 132, Achane 122, Cook 118, CMC 109, Henry 103 | JSN 122, ARSB 120 | Lamar 92, Maye 88, Hurts 85, Daniels 84 | *(21-pt cliff)* Warren 42, Loveland 38 |
| **T3** | Saquon 94, C.Brown 93, Jeanty 89, Walker 89, Love 87, Hall 82 | CeeDee 99, London 96 | Purdy 72, Nix 70, Caleb 70, Dart 66, Goff 66 | Kelce 32, Fannin 29, Kittle 25, LaPorta 22 |
| **T4** | Etienne 67, Swift 66, Kyren 66, Bucky 54 | Jefferson 67, Pickens 65, Flowers 63, AJB 62 | Herbert 59, Shough 59, Mahomes 56, Lawrence 56, Love 48, Baker 41 | — |
| **T5** | Montgomery 43, Judkins 43, Tuten 43, Henderson 38 | McMillan 55, DeVonta 55, Nico 49, Olave 46 | *(cliff)* Stroud 23, Young 19 | — |

**"Tier-2 WR vs Tier-3 RB":** a T2 WR does outscore a T3 RB — but that choice never reaches seat 1.
At every one of your picks the RBs on the board are a full tier above the WRs. Take the bigger POR;
it's the RB. It only flips in round 5+ (RB T5 ~43 vs a fallen WR T5 ~55) → take the WR then.

### TE patience

TE T1 is McBride + Bowers only; 21-POR cliff to Tyler Warren. Both gone at 56/57 → no urgency,
Warren is ~76% at pick 84. Don't reach for TE3 in rounds 5–6.

---

## Convo C — the endgame

### K / DST

`draft/hold.py`: reserve = one round per late slot the league starts (K=1, DST=1) → both held off
the default board until **round 14** (`rounds - reserve + 1` = 15 − 2 + 1). From seat 1.01 your last
two picks are **196 (round 14)** and **197 (round 15)**, back-to-back. Take K + DST there, either
order.

- No value case for earlier: 1 slot, 14 rostered, and the K1→K15 / DST1→DST15 drops are small and
  preseason-unpredictable. Every K/DST is still on the board at 196.
- The live tool hides K/DST until round 14; type `k` or `dst` to force them onto the screen.
- Real-world nuance: some grab a DST in round 13 for a good Week 1 matchup. Model says the drop is
  flat — don't sweat it.

### Late-round value — the honest version

**The value window at seat 1.01 is rounds 6–9 (picks 84, 85, 112, 113), not 12–15.** By round 12
the board's POR is negative for everything realistically available — picks 168/169 and 196/197 are
dart-throws + K + DST, and no model finds a gem there.

| Picks | What's there |
|---|---|
| 84, 85 (rd 6–7) | Tyler Warren TE (~76%, POR 42) if TE punted; RB Rhamondre / Jaylen Warren (POR ~28); WR DK Metcalf / Courtland Sutton (POR ~22) |
| 112, 113 (rd 8–9) | Travis Kelce / George Kittle (TE, POR 25–32, ~80–94%) — the TE-punt safety net |
| 140–169 (rd 10–13) | Handcuff your own RBs; swing on a rookie WR |

### "Super-low ADP players we can differentiate"

The tool for this is `draft_value` (surplus vs an ADP curve) — **but it's reading the wrong ADP**
(see bug below): its Kelce ADP is 108 vs the board's 139, Bo Nix 116 vs 30. Not trustworthy tonight.

From the board's blended view (correct ADP), where the **in-house model disagrees with market**:

- **In-house higher than market:** Jadarian Price RB (in-house 220 pts vs blended 161 — buried
  Detroit backup, but the model sees something), Sam LaPorta TE (181 vs 139), Harold Fannin TE
  (167 vs 146).
- **In-house lower (fade as depth):** Rico Dowdle RB (85 vs 157).

---

## Bugs to file after the draft

**`player_archetypes` and `draft_value` read the wrong ADP for the superflex league.**
`adp_consensus` stores a row per `format` ('1qb' / 'superflex'); `draft_board` /
`draft_availability` / `draft_plans` join on it correctly (Josh Allen 1.8, Bo Nix 30). Both
`player_archetypes` (Allen 25, Nix — via `projected_surplus`) and `draft_value` (Kelce 108 vs board
139, Nix 116 vs 30) appear not to filter on `format`. Knock-on: `projected_surplus` / surplus /
edge columns are unreliable for the sleeper league, worst for QB. All live draft-board numbers are
fine. Fix: join `adp_consensus` on `(player, format)` with `format` from `league_settings`, like
`draft_board` does. (Tried `gh issue create`; blocked by the tool classifier — file manually.)
