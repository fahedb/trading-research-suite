# Phase 8 — ULS + PUSH v7 "Adaptive" — implementation plan

**Status:** PLANE-A IMPLEMENTATION CANDIDATE · **Date:** 2026-08-25 · **Branch:**
`codex/phase8-uls-push-plan`

**Owner authorization (2026-08-25).** Implementation is authorized under the
staged scope in §7B. The owner-supplied Pine body is the semantic source of
truth: if this plan's transcription conflicts with the supplied strategy, the
Pine rule wins unless an accepted platform contract makes it unrepresentable.
Every such exception must fail closed or be recorded as an explicit deviation;
it must never be silently approximated.

> **OD-8A, OD-8B, OD-8C and OD-8D are OWNER APPROVED (2026-08-24).** Their
> answers and the complete resolution they produce are in **§7A**, which is now
> the normative configuration for this phase. **OD-8E (canonical source
> attestation) and OD-8F (exact `syminfo.mintick`) remain open** — see §7B.5
> for exactly what they do and do not block.
>
> **This family does not test the "Adaptive" behaviour.** Under the approved
> OD-8A option A2 the hour-of-day baseline and the session filter are off, and
> the strategy runs on its own rolling-global fallback. **No result from this
> phase may be represented as ULS+PUSH v7 Adaptive parity, or as a TradingView
> backtest reproduction.**

---

## 1. Provenance and what was supplied

The source is a Pine Script v5 `strategy()` titled
**"ULS + PUSH v7 | Adaptive Edition"** (`shorttitle` `ULS·PUSH7`), supplied by
the owner in-session on 2026-08-24. Its UI strings are Hebrew.

**Two provenance facts must be recorded honestly before implementation:**

1. **The repository does not hold the Pine body.** This follows the Phase-6
   precedent exactly: `zone_trader_v13` was implemented against an owner
   attestation identifying the canonical body by SHA-256, not against a
   committed copy. The same is required here — see **OD-8E**.
2. **The supplied text is truncated.** It ends mid-expression inside the
   *session-breakdown table* (`array.set(pnls, bk, array.get(pnls,`). Everything
   truncated is **display-only** (a per-session performance table). No strategy
   logic, no entry, exit, sizing or filter rule is missing. The truncation is
   therefore **not material to this plan**, but the attested canonical body must
   be complete.

---

## 2. What the strategy does, in one paragraph

ULS+PUSH is a liquidity-driven mean-reversion-into-continuation system. It
tracks swing highs/lows as **liquidity levels**, waits for price to **sweep**
one and return, then looks for a **point of interest** — either an *inverted*
fair value gap (IFVG) or a fresh **Push** zone (an impulsive displacement leg's
base) — near that sweep. When price interacts with the POI it requires a
**structure confirmation** (trendline break, break of structure, and/or CISD —
change in state of delivery), and enters on the confirmation bar's close. Stops
sit beyond the sweep extreme or the POI boundary; targets are a fixed R multiple,
an ATR multiple, or the opposite liquidity pool. The "v7 Adaptive" additions are
a **volume/range baseline bucketed by hour of day**, an **automatic session
filter**, an **automatic instrument profile**, and **timeframe-derived windows**.

---

## 3. Exact decomposition

The strategy is a five-stage state machine, run independently and symmetrically
for long and short. Long is the mirror of short throughout; this section
describes short, and every long rule is the exact mirror.

| Stage | Pine state | Rule |
|---|---|---|
| **0 · Liquidity inventory** | — | `ta.pivothigh(high, pivotLenE, pivotLenE)` produces swing highs at `bar_index - pivotLenE`. New pivots either **merge** into an existing unused level within `eqTolATR × atrSig` (incrementing `touch`, taking `max`, refreshing the bar) or push a new level. Levels expire at `liqAgeE` bars or when `used`. At most `maxLevels` are tracked. |
| **1 · Sweep** | `0 → 2` | Among unused levels (requiring `touch ≥ minEqTouch` when `liqM == 2`), select the one crossed this bar (`high > L.p and high[1] <= L.p`) with the highest rank: `2·touch + recency + proximity + prominence + equal-level bonus + key-level bonus`. Record `bsLiq`, `bsSwpBar`, `bsSwpExt`, wick ratio, volume pass, key-level proximity. |
| **2 · Return** | `2 → 3/4` | Within `reBarsE` bars price must return through the level (`swpM 1` = wick, `2` = close, `3` = close **plus** a confirming bearish close next bar). On return, reject if sweep distance `> maxSweepATR × atrSig` or `< minSwpE × atrSig`, if the volume filter fails, or if `useKlFilter` and the level is not near a key level. |
| **3 · POI** | `4 → 6` | Within `ifvgSearchE` bars of the return, find **either** an inverted bullish FVG (a `low > high[2]` gap later broken by wick or close per `sInvMode`, inverted at `f.ib ≥ bsReBar`) whose midpoint is within `maxIfvgDist × atrSig` of the swept level and which passes optional size and displacement filters, **or** — when `mergePOI` (engine "משולב") — a live unused Push short-zone within the same distance. Cancel if price closes back above the swept level or the search window expires. |
| **4 · Confirmation** | `6 → 8` | Within `breakWaitE` bars of the POI: **BOS** = close below the most recent pivot low under price; **trendline** = a rising line through the last two pivot lows with `≥ minTouches` touches within `tlTolATR × atrSig`, broken by wick or close per `sTlBreak`; **CISD** = the last bullish run's opening price broken by a bearish close, recorded at `cisdBearBar ≥ bsReBar`. `confM` selects which combination is required (7 modes). Then the EMA filter, the momentum context filter, and the optional composite-score floor are applied. Cancel if price closes above the POI top or the window expires. |
| **5 · Entry** | `8 → 9` | Entry price per `entM`: confirmation close, POI boundary (limit), or POI midpoint (limit). Stop per `slM`: beyond the sweep extreme, beyond the POI boundary, an ATR multiple, or the wider of sweep/POI — each plus `slBufATR × atrExe`, with a forced minimum so the stop is never on the wrong side. Target per `tpM`: fixed `rrTarget × R`, `tpAtrMult × atrExe`, the nearest opposite liquidity pool, or three partial R targets. |

**Push engine (independent path).** A `pushBars`-window leg qualifying on both
size (`≥ pushATRE × atrSig`) and volume (`≥ pushVolXE ×` baseline) creates a zone
from the last opposite-coloured candle before the leg (`f_base`, searching up to
12 bars back). Zones expire at `pzAge` or when price closes through them. A
retracement into a live zone between `minRetrP` and `maxRetrP` percent, with an
optional rejection wick (`rejRatioE`) and optional momentum agreement, fires a
signal with the stop at the zone boundary plus buffer.

**CISD** is tracked globally and continuously, independent of both engines.

**Cross-engine arbitration.** `cancelOpp` kills the opposite-side setup once one
side reaches state ≥ 6. `oppM` optionally closes or reverses an open position on
an opposing signal. Push signals are only taken when no ULS trade fired on the
same bar.

---

## 4. Mapping to platform layers

The pipeline is fixed ([ADR-0006](../adr/ADR-0006-fixed-stage-pipeline.md)) and
this strategy maps onto it cleanly, following the `zone_trader_v13` precedent:

```
FeatureProvider  uls_push_v7           deterministic point-in-time market structure
      ↓                                 signed event + geometry + diagnostics
ResearchModel    uls_push_gate_v1      generic numeric pass-through, no trading words
      ↓                                 ModelOutputFrame (sparse)
DecisionPolicy   deferred to conditional Plane B
      ↓                                 bracketed_exposure_intent_v1 (per-trade SL/TP)
Simulator        bracket_single_position_v1     (unchanged identity)
      ↓
TradeLog / EquityCurve → Plane-B evaluation → Explorer
```

**The methodology boundary is load-bearing here.** A Pine `strategy()` is a
complete trading system; this platform requires a *forecast* first
([RESEARCH_POLICY.md](../research/RESEARCH_POLICY.md) §7,
[ADR-0010](../adr/ADR-0010-two-plane-evaluation-and-outcomespec.md)). The
resolution is the one Phase 6 already used and which OD-8C must confirm: the
**setup-completion event is a signed forecast**, Plane A asks whether it predicts
forward-return sign, and only then does Plane B ask whether it survives execution
and costs. Entry/stop/target geometry is a **DecisionPolicy** concern and must
never enter model output as trading vocabulary
([ADR-0009](../adr/ADR-0009-generic-output-and-intentframe-family.md)).

### 4.1 Feature provider — `uls_push_v7`

Prefix `up_`, mirroring `zt_`. Emitted columns, all `float64`, all point-in-time,
`NaN` where undefined:

| Column | Meaning |
|---|---|
| `up_event_signed` | `+1` long setup confirmed, `-1` short, `0` none |
| `up_source` | `1` ULS-IFVG · `2` ULS-merged-Push-zone · `3` standalone Push |
| `up_score` | composite 0–100 (the `s1…s9` / `q1…q9` / `f_pzScore` sums) |
| `up_entry_ref` | confirmation-bar close (the reference the policy prices from) |
| `up_sweep_extreme` | sweep extreme — stop basis for `slM = 1` |
| `up_poi_top`, `up_poi_bot` | POI boundaries — stop basis `slM = 2`, entry basis `entM = 2/3` |
| `up_struct_level` | frozen BOS structure level, emitted as a diagnostic; Pine `slM = 4` instead uses the wider of the sweep extreme and POI boundary |
| `up_opp_liquidity` | nearest opposite pool — target basis `tpM = 3` |
| `up_atr_exec` | `ta.atr(atrLen)` — distance basis for `slM = 3`, `tpM = 2` |
| `up_atr_sig` | detection-threshold ATR (baseline range or ATR) |
| `up_liq_price`, `up_liq_touches` | swept level and its touch count |
| `up_sweep_dist_atr`, `up_wick_ratio`, `up_disp_atr` | sweep quality diagnostics |
| `up_vol_rel`, `up_vol_rank` | relative volume, volatility percentile |
| `up_bos`, `up_tl`, `up_cisd`, `up_key_ok` | which confirmations fired (0/1) |

**Two ATRs, deliberately.** The script separates `atrExe` (raw ATR — distances)
from `atrSig` (baseline range — detection thresholds). Both must be emitted; the
policy prices brackets from `up_atr_exec` only.

### 4.2 Model — `uls_push_gate_v1`

A pass-through gate in the shape of `zone_event_gate_v1`, but wider. It emits
only event rows, with `score = up_event_signed`, plus the 21 remaining feature
values under the neutral names `value_0` … `value_20`. The exact stable mapping
is the order in §4.1 after `up_event_signed`: `value_0 = up_source`,
`value_1 = up_score`, …, `value_20 = up_key_ok`. This avoids introducing
execution vocabulary into `ModelOutputFrame`; a future Plane-B policy must own
the semantic mapping. `zone_event_gate_v1` is unchanged.

### 4.3 Policy — deferred to conditional Plane B

The planned `uls_push_bracket_exposure_v1` is deliberately **not part of the
Plane-A implementation candidate**. If Plane A authorizes Plane B, it will
consume the generic model output and emit `bracketed_exposure_intent_v1`.
Its proposed identity-bearing
params (price units throughout — never pips, so no cost-binding conversion enters
the decision layer):

```
stop_mode          : "sweep" | "poi" | "atr" | "structure"
stop_buffer_atr    : float >= 0
stop_atr_mult      : float > 0            # stop_mode = "atr"
target_mode        : "rr" | "atr" | "opposite_liquidity"
target_rr          : float > 0
target_atr_mult    : float > 0
min_score          : float | None          # None = no score floor
on_invalid_bracket : "abstain"             # frozen; see §8
```

For fidelity, `stop_mode = "structure"` must implement Pine's actual formula:
the wider of the sweep extreme and POI boundary, not `up_struct_level`.
Distances are computed from the model's own point-in-time geometry against
generic `value_2` (`up_entry_ref` at the feature boundary), **never** against
the fill price (which is `open[T+1]` and therefore future at decision time).

### 4.4 Execution

Conditional Plane B would use `bracket_single_position_v1`, unchanged, with
`ExecutionSpec.take_profit_pips = null` and `stop_loss_pips = null` so every
bracket must arrive through the intent
([ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) Amendment 1).

---

## 5. What is representable, and what is not

This is the honest gap table. **The script's own defaults are shown**, because
what matters is whether the strategy *as configured by default* can run.

### 5.1 Representable today

| Script capability | Platform mechanism |
|---|---|
| Full ULS state machine, both directions | feature provider |
| Push engine and merged POI | feature provider, `engine_mode` param |
| Pivot liquidity, equal-level merging, touch counts, ageing | feature provider |
| FVG → IFVG inversion, size/distance/displacement filters | feature provider |
| BOS · trendline · CISD, all 7 `confM` combinations | feature provider |
| Composite score and score floor | feature provider |
| EMA filter, current timeframe | finite-window EMA path in the staged provider |
| Volume, displacement, key-level filters | feature provider |
| Stops: sweep / POI / ATR / Pine's wider sweep-or-POI mode + buffer | representable by a conditional per-trade intent policy; not in Plane A |
| Targets: fixed R:R, ATR multiple, opposite liquidity | representable by a conditional per-trade intent policy; not in Plane A |
| One position, no pyramiding | simulator invariant, conditional Plane B |
| Opposite-signal close or reverse | platform-capable but deferred position-aware policy work |
| Conflict cancellation | feature provider state machine |

### 5.2 NOT representable — and whether the default needs it

| Script capability | Default | Blocking? | Why not representable |
|---|---|---|---|
| **Hour-of-day volume/range baseline** | **ON** (`useHourBase = true`) | **YES → OD-8A** | No calendar decoding exists anywhere. `CalendarTimePolicy.NATIVE_WALLCLOCK_V1` is an *identity only* — `core/time.py` states "no decoding, no conversion, and no calendar arithmetic exists yet", and the features layer cannot reach it. |
| **Session window filter** | **ON** for XAUUSD intraday (`"אוטומטי"`) | **YES → OD-8A** | Same root cause, plus [ADR-0005](../adr/ADR-0005-gapindex-not-sessionmodel.md) forbids promoting observed gaps to session facts. |
| Higher-timeframe EMA (`request.security`) | OFF | no | Deferred in the staged provider; selecting it fails closed. |
| Retest limit entries (`entM = 2/3`) | OFF (`entM = 1`) | no | Simulator fills market at the next observed row's open. No limit orders. |
| Partial exits (`tpM = 4`) | OFF (`tpM = 1`) | no | One immutable bracket per trade; no partial exits. |
| Trailing stop / breakeven | OFF (both) | no | The bracket is immutable for the life of the trade, by construction. |
| Percent-risk sizing | OFF (fixed qty) | no | `ExecutionSpec` is fixed-lot only. |
| Auto instrument profile (`syminfo.ticker` sniffing) | ON (`"אוטומטי"`) | no | Platform is single-symbol; resolve **once at config-freeze time** into explicit params. Auto-detection at runtime is forbidden — it would make identity depend on the symbol string. |
| Timeframe-derived windows (`reBarsE`, `ifvgSearchE`, `breakWaitE`, `totalBarsE`, `liqAgeE`, `minSwpE`) | derived from `tfSec` | no | Resolve **once at config-freeze time** into explicit params. This is why **OD-8B** must fix the trading timeframe. |
| Symbol minimum tick (`syminfo.mintick`) | symbol metadata | **YES → OD-8F before S-10** | It is not present in OHLCV. The provider exposes identity-bearing `minimum_tick`; `0.01` is provisional until the exact TradingView symbol is confirmed. |
| Slippage model (`slippage = 5`) | ON | no | No slippage framework exists ([ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) §2). Declared limitation. |
| Drawings, dashboard, session table | ON | no | Display only; the Explorer already covers presentation. |

### 5.3 One semantic difference that is not a gap

Pine runs with `process_orders_on_close = true`, so a signal on bar *T* fills at
**bar T's close**. The platform fills at **bar T+1's open**
([ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) §4). This is a real
and permanent difference of roughly one tick plus any gap. It is *more*
conservative, it is the platform's accepted no-lookahead rule, and it must be
recorded — **no document may claim parity with a TradingView backtest.**

---

## 6. Reach, leakage and determinism

**Declared reach is the release-blocking property.** `WindowSpec.max_prior_rows`
must bound every backward read, and the platform enforces it by overwriting the
first `max_prior_rows` evaluation rows with `NaN` after `compute` returns.

Contributors to reach, all in **observed M1 rows**:

| Contributor | Bound |
|---|---|
| ATR / EMA / momentum EMA seeding | `max(atrLen, emaLen, momLen) × bars_per_signal_bar`, truncated-window (the Phase-6 G-2 precedent) |
| HTF EMA on a 3600 s arithmetic grid | `emaLen × 3600 / 60` M1 rows |
| Pivot detection | `pivotLen` bars either side |
| Liquidity ageing | `liqAgeE` signal bars |
| FVG memory | `fvgMaxAge` signal bars |
| Push zone ageing and `f_base` lookback | `pzAge + 12` signal bars |
| Volatility percentile | `min(500, max(100, bars_per_day × 10))` |
| **Hourly baseline (if OD-8A permits)** | `min(2000, bars_per_hour × baseDays)` — the deepest single contributor |

`max_prior_rows` = the maximum of these, converted to M1 rows, **plus a stated
safety margin**, exactly as Phase 6 declared 26,534 (conservative by 8 rows) and
tested alignment and influence separately.

**Leakage rigs must be extended before the provider is registered**, per
`CLAUDE.md` ("extend leakage rigs when adding any provider/model"). Phase 6 slice
S-1 is the precedent: enlarge fixtures by the declared reach and add adversaries
on every declared input column.

**The `used` flags are the subtle leakage risk.** `Liq.used`, `Fvg.used` and
`PZ.used` are mutable state carried across bars. The provider must be a pure
function of the bounded request — no module-level state, and the state machine
must be rebuilt from the start of the context window on every call. Determinism
verification (cache-bypassing double execution) is what proves it.

**Nothing here may read forward.** `ta.pivothigh(high, n, n)` is confirmed `n`
bars late and must be emitted at `bar_index - n`, never back-dated — the same
rule Phase 6 fixed as ID-1 (earliest-provable-completion).

---

## 7. Owner decisions required — BLOCKING

### OD-8A — the adaptive baseline and the session filter · **CRITICAL**

`useHourBase = true` and an automatic session window are the defining features of
"v7 Adaptive", and **both are on by default**. Neither is expressible today.

Mechanically, an hour bucket is reachable as pure integer arithmetic on the
native key — `(key // 3600) % 24` — which is the same shape as `zone_trader_v13`'s
arithmetic grids and the same decode the Explorer already performs. It is *not* a
UTC claim and *not* a session claim. But the platform has deliberately shipped no
calendar arithmetic, and `CalendarTimePolicy` exists precisely to record a
calendar-shaped assumption rather than let it be assumed.

**Options:**
- **A1 — implement it under a new ADR.** Add native wall-clock hour-bucket
  arithmetic to the feature layer, declared under `native-wallclock-v1`, named
  honestly ("native wall-clock hour bucket", never "UTC hour"), with a session
  window expressed as an explicit half-open bucket range and never as a market
  session claim. Highest fidelity; costs an ADR and a leakage-rig extension.
- **A2 — drop both for V1 (recommended for the first pass).** The script already
  contains the fallback: when `bReady` is false it uses the rolling global
  baselines `volGlb` / `rngGlb`, and `sessOn = false` disables the window. This is
  a *supported code path of the strategy itself*, not an invention. It makes the
  first implementation strictly smaller and defers the ADR — at the cost of not
  testing the "Adaptive" claim.
- **A3 — defer the whole strategy** until the calendar question is settled
  independently.

**A decision is required because A1 and A2 are different strategies**, and no
result from one transfers to the other.

### OD-8B — the trading timeframe · **CRITICAL**

Six windows (`reBarsE`, `ifvgSearchE`, `breakWaitE`, `totalBarsE`, `liqAgeE`,
`minSwpE`) branch on `tfSec ≤ 300` / `≤ 900` / else, and `pivotLenE`, `pushATRE`,
`pushVolXE` branch on the instrument profile. The platform stores **M1** and
builds coarser bars by arithmetic grid, so the intended signal timeframe must be
fixed once and frozen into explicit params.

**What is the strategy meant to trade — M5, M15, or something else?** Phase 6
used an M5 signal grid with an H1 anchor on the M1 axis; the same shape works
here. Without this answer every window in the provider is undetermined.

### OD-8C — the Plane-A hypothesis · **CRITICAL**

Confirm the Phase-6 framing: `up_event_signed` is treated as a **signed forecast**
and evaluated on Plane A (`sign_accuracy_v1`, `rank_ic_v1`) against a forward
log-return outcome **before** any Plane-B money result is produced or discussed.

If the owner instead intends to go straight to Plane B, that is a deliberate
departure from RESEARCH_POLICY §7 and must be recorded as such — the simulator is
second, never first, and a profitable-looking bracket over a signal with no
Plane-A edge is a curve fit with extra steps.

### OD-8D — parameter freeze and the research denominator

The script exposes roughly **90 inputs**. Under
[ADR-0024](../adr/ADR-0024-multiple-testing-trial-ledger.md) every swept
parameter inflates the trial denominator, and a 90-parameter search cannot be
honestly reported.

**Recommendation:** freeze **one** default vector taken directly from the script's
own defaults (resolved for the OD-8B timeframe), preregister it, and run it once.
Any subsequent variation is a new preregistered family with its own frozen
request — exactly as the Zone Trader fixed-bracket families were handled.

### OD-8E — canonical source attestation

Following Phase 6: the owner attests, by SHA-256, which Pine body is canonical.
The repository will not hold the Pine source. The supplied text is truncated in
its display section (§1), so the attested body must be the complete file.

### OD-8F — exact TradingView `syminfo.mintick` · **CRITICAL BEFORE S-10**

The Pine body uses `syminfo.mintick` when it consumes a swept liquidity level,
protects small denominators and applies execution fallback distances. This
metadata is not present in the platform's OHLCV input. The staged provider
therefore exposes `minimum_tick` explicitly and currently uses **0.01 only as a
provisional XAUUSD value**. The owner must confirm the value shown by the exact
TradingView symbol before the first family is frozen; it must not be inferred
from observed price decimals.

---

## 7A. Owner decisions — APPROVED, and the configuration they resolve to

**Approved 2026-08-24.** This section is normative. Where it differs from §7's
option descriptions, this section wins.

### 7A.1 The four approved decisions

| | Decision | Approved answer |
|---|---|---|
| **OD-8A** | Adaptive baseline / session filter | **Option A2.** Use the Pine strategy's own rolling global volume/range fallback; disable the session filter. **Do not add civil-time or hour-of-day infrastructure.** Record explicitly that this family does not test the full Adaptive hour-of-day/session behaviour and must not be represented as v7 Adaptive parity. |
| **OD-8B** | Signal timeframe | **M5, fixed.** This is **strategy identity, not a sweep parameter** — it is frozen into the provider's params and never varied within this phase. |
| **OD-8C** | Plane-A first | **Approved.** The signed ULS/PUSH event is the forecast; evaluate it with the existing forward-return outcome on Plane A. **Plane B is conditional on the Plane-A result.** |
| **OD-8D** | Parameter freeze | **Approved.** One frozen vector from the Pine defaults. **No initial sweep.** Any later variation is a separately recorded research family and trial. |

### 7A.2 What OD-8A switches off, mechanically

These are settings of the supplied strategy, not modifications of it:

```
useHourBase = false      ->  hourBaseOk = false  ->  bReady = false
                         ->  volRef = volGlb = SMA(volume, 20)
                         ->  rngRef = rngGlb = SMA(TR, 20)
sSessMode   = "off"      ->  sessOn = false      ->  inSess  = true on every row
minVolRank  = 0          ->  regimeOk = true on every row
```

`tradeOk` therefore reduces to the date bound alone, which the platform's fold
geometry already supplies. **No hour bucket, no session window, no calendar
arithmetic, and no `CalendarTimePolicy` use anywhere in this phase.**

**The two ATRs still differ and both are still required:**

| | Definition | Used for |
|---|---|---|
| `atrExe` | `RMA(TR, 14)` — Wilder | bracket **distances**: stop buffer, ATR stop, ATR target |
| `atrSig` | `SMA(TR, 20)` — the `rngGlb` fallback | **detection thresholds**: sweep bounds, POI distance/size, tolerances, key-level proximity |

Collapsing them into one ATR would silently change every threshold in the
strategy. They are different statistics and must stay separate.

### 7A.3 M5 resolution — every derived constant, computed

`tfSec = 300`, so `barsPerHour = 12` and `barsPerDay = 288`. Taking the script's
own branches at M5, with the auto profile resolving to the XAUUSD arm
(`autoX`, because the ticker contains `XAU`):

| Script symbol | Resolved value | Source branch |
|---|---:|---|
| `reBarsE` — bars allowed for the return | **3** | `tfSec <= 300` |
| `ifvgSearchE` — POI search window | **6** | `tfSec <= 300` |
| `breakWaitE` — POI → confirmation window | **12** | `tfSec <= 300` |
| `totalBarsE` — whole-setup expiry | **40** | `tfSec <= 300` |
| `liqAgeE` — liquidity level max age | **800** | `tfSec <= 300` |
| `minSwpE` — minimum sweep (ATR) | **0.10** | `max(minSweepATR, 0.10)` |
| `pivotLenE` | **5** | `autoX` |
| `pushATRE` | **1.5** | not the BTC arm |
| `pushVolXE` | **1.5** | not the BTC arm |
| `rejRatioE` | **0.45** | always |
| `volRank` window | **500** M5 | `min(500, max(100, barsPerDay × 10))` |

**These become explicit provider params with these literal values.** No runtime
timeframe branch and no runtime ticker sniffing survives into the implementation
— identity must never depend on a symbol string or a bar size.

### 7A.4 What the first frozen research vector switches off

Taking the remaining Pine defaults, large parts of the first research vector
are inert. §7B later authorized those time-independent paths as provider
switches for future, separately preregistered families; they remain off in the
first vector and do not enlarge its active reach.

| Default | Consequence |
|---|---|
| `sEmaFilter = "off"` (`emaM = 0`) | **No EMA filter in the first vector.** The staged current-timeframe switch in §7B is inactive; `request.security` and HTF aggregation remain unavailable. |
| `useMomCtx = false` | `momUp`/`momDn` are always true. **No momentum EMA.** |
| `useVol`, `useDisp`, `useScore`, `useIfvgSize` all `false` | No volume, displacement, score-floor or IFVG-size gate. The underlying quantities are still computed because they feed the composite score and the diagnostics. |
| `useKlFilter = false` (with `useKL = true`) | Key levels are **not** a hard filter, but `bsKeyOk` still feeds level ranking and the score, so key-level arithmetic is still required. |
| `useBE = false`, `useTrail = false` | Nothing to represent; the bracket is immutable anyway. |
| `sSizeMode = fixed`, `entM = 1`, `slM = 1`, `tpM = 1` | Fixed lot · market entry at the confirmation bar · **stop at the sweep extreme + `slBufATR × atrExe`** · **target at `rrTarget = 2.0 × R`**. All representable. |
| `sOppSignal = "ignore"` (`oppM = 1`) | **No opposite-signal close or reverse.** |
| `sEngine = "merged"` (`engM = 4`) | `ulsOn = true`, `mergePOI = true`, and — importantly — **`pushOn = false`**. |

**Two consequences of `engM = 4` that are easy to get wrong:**

1. **Standalone Push entries never fire.** `up_source = 3` is unreachable in this
   family. Push zones exist *only* as an alternative POI for the ULS machine.
2. **The Push retracement logic must still be implemented anyway.** It sets
   `PZ.used`, and the merged-POI path selects on `z.active and not z.used`. A
   zone consumed by a (non-trading) Push retracement is therefore unavailable to
   ULS. Omitting the retracement logic because "Push is off" would silently
   change which POIs the ULS machine can select. This coupling is the single most
   likely implementation error in the whole phase.

### 7A.5 Declared reach at M5, derived — superseded by §7B.4

Reach is the **longest dependency chain**, not the longest single window. A
signal at row *T* may rest on a liquidity level up to `liqAgeE + pivotLenE` M5
bars old, and that level required a valid ATR when it was created, which needs
its own warm-up. Using the Phase-6 G-2 precedent of a truncated 300-bar window
for the Wilder ATR approximation:

| Chain | M5 bars | M1 rows |
|---|---:|---:|
| **liquidity age + pivot confirmation + ATR warm-up** | **1,105** | **5,525** |
| volatility percentile window + ATR warm-up | 800 | 4,000 |
| FVG memory + ATR warm-up | 600 | 3,000 |
| Push zone age + base lookback + `pushBars` + ATR warm-up | 518 | 2,590 |

Dominant chain **5,525 M1 rows**, plus 5 for M5 bucket completion under ID-1
(a bucket is usable only once a later-bucket row has been observed).

This original `5,600`-row derivation omitted the permitted 40-M5-bar interval
from sweep selection to setup completion and is therefore superseded. The
corrected derivation and dynamic declaration are normative in §7B.4.

**D-1 — required deviation: pivot memory must be age-bounded.** The Pine arrays
`phP`/`plP` are capped at 30 *entries* with **no age bound**, so a pivot of
arbitrary age can still drive BOS and trendline construction and the reach is
unbounded in principle. A finite declared reach is mandatory here, so pivots
older than `liqAgeE` are dropped, matching the liquidity levels' own ageing.
This is a stated, tested deviation, not a silent one, and it must be listed
alongside the Phase-6 deviations in the research document.

## 7B. Authorized staged implementation — normative amendment (2026-08-25)

This section supersedes any earlier statement that the frozen default vector is
the only provider behaviour that may be implemented. The first research family
remains one frozen default vector, but the plugin may expose the Pine strategy's
representable, time-independent switches so they can be enabled in later,
separately preregistered families.

### 7B.1 Exact M5 and input semantics

- Reuse the implemented complete-bar aggregation predicate A: an M5 bucket is
  usable only after an observed row in a later bucket exists.
- `bar_grid_seconds = 300`, `bar_phase_offset_seconds = 0`, both
  identity-bearing; `GapPolicy.CROSS`, `nominal_interval = None`.
- Pine `volume` maps to `tick_volume`. Required market columns are exactly
  `open`, `high`, `low`, `close`, `tick_volume`.
- Pine `syminfo.mintick` maps to the explicit identity-bearing
  `minimum_tick`. Its provisional value is `0.01`; OD-8F blocks freezing it.
- No runtime symbol sniffing and no runtime timeframe branch. XAUUSD/M5 values
  remain explicit parameters.

### 7B.2 Supported switches in the staged provider

The provider supports the four Pine engine modes; structural/equal liquidity;
all three sweep confirmations; IFVG wick/close inversion and optional size
filter; Push-zone geometry, retracement and rejection; key levels; all seven
structure-confirmation combinations; current-timeframe EMA and momentum
context; volume, displacement, score and volatility-regime filters; and the
Pine parameters that govern those paths. Standalone Push events are therefore
reachable only when the selected engine mode enables them.

The following settings fail closed in this stage: hour-of-day baseline, UTC
session filtering, higher-timeframe EMA, retest/limit entry, partial exits,
breakeven, trailing stop, percent-risk sizing, slippage reproduction, and every
position-dependent opposite-signal mode. Display settings are not plugin
semantics. Plane A is implemented first; position-aware Plane B behaviour is
deferred until Plane A supplies evidence worth simulating.

### 7B.3 Finite recursive indicators

Pine's recursive RMA/EMA has unbounded mathematical memory. The provider uses
the algebraically equivalent finite-window weights directly (not a rolling
subtract-and-cancel recurrence), so observations outside the declared window
have **zero bitwise influence**. The omitted source weight is below roughly
`exp(-20)` at the default lengths:

- ATR/RMA window: `max(300, 20 * atr_len)` M5 bars.
- current-timeframe EMA window: `max(2_000, 10 * ema_len)` M5 bars when enabled.
- momentum EMA window: `max(500, 10 * mom_len)` M5 bars when enabled.

These lengths are derived implementation parameters, enter provider identity
through their source parameters, and enter `WindowSpec`. They are controlled
approximations, not TradingView-parity claims.

### 7B.4 Corrected finite state and reach

Two additional owner-approved deviations are required to make state reach
finite:

1. A merged liquidity level expires `liq_age` M5 bars after its **first** pivot;
   a later equal-level touch updates its Pine price/last-touch fields but does
   not reset the lineage age.
2. CISD run memory expires after `cisd_max_age = 800` M5 bars.

The default longest chain is liquidity lineage `800` + whole-setup lifetime
`40` + pivot confirmation `5` + ATR window `300` = **1,145 M5 bars**. Using the
already accepted complete-M5 conversion:

```
max_prior_rows = 5 * (R_M5 + 2) - 1
               = 5 * (1_145 + 2) - 1
               = 5_734 M1 rows
```

When current EMA or another configurable bounded path is enabled, `R_M5` is the
maximum of every active dependency chain and the provider derives the larger
declaration. S-6 must measure the default boundary and at least one enlarged
EMA boundary independently.

**S-6 measurement (2026-08-25).** A public-provider short ULS fixture places a
liquidity pivot at its exact 800-M5 age limit and confirms through IFVG + BOS.
Its oldest observed pivot-leg influence is 4,045 M1 rows before the projected
event, versus the default declaration of 5,734. With current EMA enabled, a
separate public event changes when the oldest member of the 2,000-M5 EMA window
is changed at 10,000 M1 rows, is bitwise unchanged for every earlier row, and is
bounded by the enlarged declaration of 10,009. Repeated calls are bitwise exact
across every output column.

### 7B.5 Fold geometry, and what remains open

**Reuse the existing TRAIN geometry unchanged** — `context_start = 1577926800`,
`eval_start = 1580437860`, `fit_rows = 385824`, five contiguous 200,000-row
folds. That window already accommodates a reach of 28,714 rows, so 5,734 fits
with very large headroom, and reusing it keeps this family directly comparable
with the existing Zone Trader families on the same axis. Dataset:
`XAUUSD_S_M1_TRAIN_V1` only.

**OD-8E (canonical source attestation) and OD-8F (exact `syminfo.mintick`) are
still open.** Proportionate handling means they block only what they should:

- **S-1 … S-9 may proceed.** The truncated portion of the supplied text is
  display-only (a per-session performance table) and contains no strategy rule,
  so the provider, model and policy can be built and tested against the supplied
  body.
- **S-10 must not freeze the research family until both answers land.** A
  preregistered family has to name the exact source it implements, by SHA-256,
  as Phase 6 did. Record it as an owner source attestation, not as an
  independent byte comparison — the repository will not hold the Pine body.

**Owner-authorized exploratory exception (2026-08-25).** After being told both
open provenance inputs, the owner explicitly instructed the agent to continue
and run the first experiment. This authorizes one TRAIN-only exploratory
Plane-A family with `minimum_tick = 0.01`, the current implementation candidate
and the source limitations stated in
`docs/research/ULS_PUSH_V7_TRAIN_EXPLORATORY_V1.md`. It does **not** resolve
OD-8E, independently verify OD-8F, complete formal S-10, authorize a sweep, or
permit VALIDATION/OOS/LOCKBOX/Plane-B access. The run remains a counted trial
and cannot later be hidden or replaced.

### 7B.6 Research-integrity consequences of these approvals

- **Plane A gates Plane B (OD-8C).** No money figure, and no Plane-B evaluator,
  until the Plane-A result exists and is recorded. If Plane A shows no
  consistent signed information, the honest outcome is to stop — as H1 did for
  Zone Trader.
- **One vector, one trial (OD-8D).** The frozen vector is a single hypothesis.
  Under ADR-0024 any later variation is a separate preregistered family with its
  own frozen request; it is never a "re-run with a tweak".
- **The Adaptive claim is untested (OD-8A).** This must appear in the research
  document, in `PROJECT_STATE.md`, and in any summary of the result. The
  strategy's headline feature is switched off in this family.

**Owner-directed fixed-bracket exception (2026-08-26).** After receiving the
negative S-9E/S-11E result and its stopping-rule explanation, the owner
explicitly requested a simulator run with TP400 and SL400. This authorizes one
TRAIN-only Plane-B configuration using the existing provisional fixed-bracket
cost binding. It is a visible methodology exception, not evidence that Plane A
passed: it cannot reverse the negative forecast result, authorize a TP/SL
sweep, or permit VALIDATION/OOS/LOCKBOX. The exact assumptions and mandatory
unverified-execution/money caveats are frozen in
`docs/research/ULS_PUSH_V7_TRAIN_PLANE_B_TP400_SL400_V1.md`.

**Owner-directed 200 × 4 TRAIN sweep exception (2026-08-26).** After the fixed
TP400/SL400 result was recorded as negative, the owner explicitly requested 200
ULS + PUSH strategy-setting combinations under four supplied bracket pairs:
TP200/SL200, TP800/SL800, TP600/SL300 and TP300/SL600. This authorizes exactly
800 TRAIN-only Plane-B configurations frozen together in
`docs/research/ULS_PUSH_V7_OWNER_TRAIN_200X4_BRACKETS_V1.md`. It supersedes the
earlier no-sweep instruction only for this visible exploratory family. It does
not reverse Plane A, establish independent confirmations, resolve OD-8E/OD-8F,
or permit VALIDATION/OOS/LOCKBOX access.


## 8. Design decisions taken without asking

These are recorded so no agent re-opens them. None changes a result.

1. **Prefix `up_`** for feature columns, mirroring `zt_`.
2. **Price units, never pips**, in every policy parameter — `price_units_per_pip`
   belongs to the cost binding and must not enter the decision layer.
3. **`on_invalid_bracket = "abstain"`** — a setup whose geometry yields a
   non-positive or inverted distance produces an explicit abstention rather than
   an unbracketed trade. This follows `BracketThresholdPolicy`.
4. **Engine mode is one policy/feature parameter**, not four implementations —
   the four `sEngine` values share all machinery.
5. **Auto-profile and TF-derived windows are resolved at config-freeze time**,
   never at runtime, so identity never depends on a symbol string or a bar size.
6. **Display, dashboard and session-breakdown table are out of scope.** The
   Explorer already renders entries, exits, brackets and exit reasons.
7. **`zone_event_gate_v1` and every existing policy are untouched.**
8. **Non-default execution modes are refused, not approximated** — a config
   selecting retest entry, partial exits, trailing, breakeven or percent-risk
   sizing must fail at resolution with a message naming the unsupported mode.
   Silently downgrading them would make an unaudited assumption look like a
   platform semantic.

---

## 9. Ordered implementation slices

Each slice leaves the repository green and is independently reviewable.

| Slice | Objective | Files | Acceptance |
|---|---|---|---|
| **S-0** | **DONE** — OD-8A…OD-8D approved and resolved in §7A. No ADR is needed: option A2 adds no calendar infrastructure | `PROJECT_STATE.md`, this document | Decisions recorded before any code |
| **S-1** | **DONE** — extend leakage rigs for dynamically declared reaches; add adversaries for every input column the provider declares | `tests/test_feature_leakage.py` | Registry coverage, future mutation, seam, reach and dense-axis refusal are green |
| **S-2** | **IMPLEMENTED CANDIDATE** — `uls_push_v7` liquidity inventory, sweep, return | `features/reference.py`, `tests/test_feature_execution.py` | Core paths implemented; exact-golden hardening continues under S-6 |
| **S-3** | **IMPLEMENTED CANDIDATE** — FVG → IFVG, Push zones, `f_base`, zone lifecycle | same | Standalone/merged Push and IFVG transition fixtures are green |
| **S-4** | **IMPLEMENTED CANDIDATE** — CISD, BOS, trendline touch counting, all 7 `confM` modes | same | State transition and all seven combination rules are pinned |
| **S-5** | **IMPLEMENTED CANDIDATE** — score, key levels, distinct ATRs, volatility percentile, current EMA and momentum switches | same | Default and optional finite paths are implemented; HTF EMA fails closed |
| **S-6** | **DONE** — declared-reach measurement and deterministic replay | `tests/test_feature_execution.py`, `tests/test_feature_determinism.py`, `tests/test_feature_leakage.py` | Public event boundaries measure 4,045 ≤ 5,734 default and 10,000 ≤ 10,009 with current EMA; earlier mutations and repeated calls are bitwise exact |
| **S-7** | **DONE** — `uls_push_gate_v1` model | `models/reference.py`, `tests/test_models.py`, `tests/test_model_leakage.py` | Sparse generic `score` + `value_0…value_20`; banned-vocabulary and leakage tests green |
| **S-8** | **DEFERRED TO CONDITIONAL PLANE B** — `uls_push_bracket_exposure_v1` policy | `decisions/reference.py`, `tests/test_decisions.py` | Not implemented or registered in Plane A |
| **S-9** | **DEFERRED WITH PLANE B** — config-resolution refusals for position-dependent execution modes | `experiments/config.py`, `tests/test_experiments.py` | Provider-level hour/session/HTF refusals are implemented; execution-mode resolution remains closed |
| **S-9E** | **DONE** — freeze and execute one provenance-limited exploratory Plane-A family on TRAIN only | `config/experiments/uls-push-v7-h20m5-train-exploratory-v1.json`, `docs/research/ULS_PUSH_V7_TRAIN_EXPLORATORY_V1.md` | Canonical run `01M0X915JS4H9EBDJQ5NFDFGR9`; one vector, H100M1 outcome, no sweep, no simulator, explicit source/minimum-tick limitations |
| **S-10** | Freeze the preregistered **Plane-A** TRAIN family — one vector, no sweep. **Requires OD-8E and OD-8F** | `config/experiments/`, `docs/research/ULS_PUSH_V7_TRAIN_V1.md` | Frozen in a clean commit before execution; source attested by SHA-256; exact minimum tick and the OD-8A caveat recorded |
| **S-11** | Execute, verify, record the **Plane-A** result | `PROJECT_STATE.md`, research doc | Determinism exact; Trial Ledger clean; no OOS/LOCKBOX. **Stop here and report.** |
| **S-11E** | **DONE FOR THE EXPLORATORY FAMILY** — verify and record S-9E | `PROJECT_STATE.md`, exploratory research doc | 27 exact comparisons, maximum deviation 0.0; two Plane-A trials; no consistent positive TRAIN evidence; no Plane-B basis |
| **S-12** | **Conditional on S-11 only** — Plane-B family with per-trade brackets | `config/`, research doc | Authorized only if Plane A showed consistent signed information (OD-8C) |
| **S-12E** | **DONE — NEGATIVE GROSS AND NET** — one fixed TP400/SL400 TRAIN simulation, owner-authorized despite negative Plane A | `config/experiments/uls-push-v7-train-plane-b-tp400-sl400-v1.json`, dedicated research doc | Run `01M0YE5SE5YR24P9XN5RXZ3XTR`; 1,817 trades; -187.57 gross, 472.42 provisional cost, -659.99 net; exact verification; no protected data |
| **S-13E** | **DONE — 800/800 COMPLETE** — owner-directed 200-vector × four-bracket TRAIN sweep | generated sweep request, generator, preregistration test, dedicated research doc | Zero failures/duplicates/protected access; all bracket mean/median net results negative; TP800/SL800 strongest descriptively; Trial Ledger clean; Explorer rebuilt |

---

## 10. Test strategy

**Ordinary unit tests:** exact hand-computed goldens for every Plane-A stage — pivot
emission offset, equal-level merge and touch counting, sweep ranking order,
min/max sweep rejection, FVG formation and inversion by wick and by close, zone
base selection, retracement percentage bounds, rejection-wick ratio, each `confM`
combination, score decomposition, and both ATRs. `slM`, `tpM`, retest and
position-management goldens belong to conditional Plane B.

**Release-blocking rigs:**

1. **Declared reach** — the provider must not read beyond `max_prior_rows`;
   measured actual reach reported and shown ≤ declared.
2. **Feature leakage** — adversaries on every declared column, fixtures enlarged
   by the declared reach (Phase-6 S-1 precedent).
3. **Pivot causality** — a pivot must never be emitted before it is provable,
   i.e. never earlier than `bar_index - pivotLen`.
4. **Statelessness** — two calls on the same request produce bitwise-identical
   output; no module-level mutable state survives a call.
5. **DecisionPolicy prefix-invariance (Plane B only)** — automatic via
   `POLICY_RECIPES`; a future policy must be added there or the inventory test
   fails.
6. **Determinism** — cache-bypassing double execution, maximum absolute deviation
   `0.0`.
7. **Unsupported-mode refusal** — time/session/HTF modes fail in Plane A;
   position-dependent modes remain unavailable until their conditional Plane-B
   resolver exists.

---

## 11. Non-goals

No limit or stop-entry orders · no partial exits · no trailing stop · no
breakeven · no percent-risk sizing · no slippage model · no multi-symbol · no
runtime instrument detection · no runtime timeframe branching · no session or
holiday inference · no UTC conversion · **no hour-of-day baseline and no
calendar arithmetic of any kind (OD-8A)** · no `CalendarTimePolicy` use · no
`request.security` or higher-timeframe EMA · current-timeframe EMA, momentum
and standalone Push exist only as explicit provider switches and remain off in
the first frozen vector · no parameter sweep except the explicit owner-directed
S-13E TRAIN family · no Plane-B evaluation before Plane A except the recorded
owner-directed S-12E/S-13E methodology exceptions · no feature-of-feature path · **no claim of parity with a
TradingView backtest or with v7 Adaptive** · no money figure quoted as a broker
estimate while the cost audit is open.

---

## 12. Exact next action

**Stop after the completed S-13E TRAIN search and report.** All 800 children are
recorded. Every bracket's mean and median net result is negative; TP800/SL800 is
the strongest descriptive bracket but its selected maximum changes sign across
folds, and its only five-fold-positive candidate has 98 trades. Any validation
step requires a separately frozen candidate set and explicit owner decision.
Do not continue adapting candidates on TRAIN. Formal S-10 still requires OD-8E
and OD-8F.

Do not access VALIDATION, OOS or LOCKBOX at any point in this phase without a
separate, explicitly audited authorization. Do not run a Plane-B evaluation
before the Plane-A result exists and is recorded.
