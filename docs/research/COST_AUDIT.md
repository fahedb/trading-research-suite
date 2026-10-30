# Cost-Semantics Audit — evidence record

Date opened: 2026-08-14 (Phase-4 planning) · Status: **OPEN — NOT CLOSED**
Revision 1: 2026-08-14 (pre-merge review remediation — see §0)
Normative gate: [ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md) and its
Amendment 1 · Contracts: [ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) ·
Plan: [PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md)

> **This document is the only place in the repository that may record a cost
> fact as established.** Every fact V1's arithmetic consumes is either
> **VERIFIED** — with the exact evidence that established it — or **UNKNOWN**,
> with the exact evidence still required. **No component may assume a value for
> an UNKNOWN fact**, and a plausible default is worse than no default, because
> it produces a defensible-looking number nobody can defend. Facts about
> effects V1 deliberately does not model or enforce are classified separately
> (§1.1) and stay visible as declared limitations rather than pretending to be
> pending evidence.

## 0. Revision 1 (2026-08-14) — what changed and why

A pre-merge review of the Phase-4 planning branch raised four findings. Three
of them bear on this record and are corrected here; the fourth (RM-4 — Slice
4A / CP-13 status versus the exact next action) is recorded in
[PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md) §1.1 and resolves as:
**Slice 4A baseline record COMPLETE · CP-13 GREEN (evidence-classification
gate) · this audit remains OPEN for external evidence · closure is the separate
checkpoint CP-13C · next implementation action is Slice 4B.** **No
owner-approved decision (OD-4A…OD-4K) changed.**

| # | Defect | Correction |
|---|---|---|
| RM-1 | The plan bound the executable price path permanently — "costs are money charges, so triggering is unaffected" — even though a one-sided recorded series (A6) would displace the executable entry price *and* the bracket levels derived from it. | The executable-price-path semantics are separated from the monetary arithmetic and are **explicitly UNBOUND** until A6/B3 are VERIFIED. The current model is a **named provisional binding**, not the permanent contract (§3.0, [ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) §8). |
| RM-2 | The closure rule ("every fact in §3 and §4 VERIFIED") was internally impossible: §4 deliberately contains NOT MODELLED / NOT APPLICABLE items that will never be VERIFIED. | Facts are split into **closure-required (CR)** and **non-blocking declared limitations (NB)**. The closure rule is stated over CR only and is mechanically satisfiable (§1.1). |
| RM-3 | `cost_semantics: verified` could be declared by a config while the audit was OPEN; the plan called that "a documentation error", not a refusal. | **Fail-closed:** VERIFIED is only constructible against a repository-controlled binding constant that exists solely after closure. While it does not exist, VERIFIED is a hard refusal (§3.0). |

## 1. How to read this record

| Status | Meaning |
|---|---|
| **VERIFIED** | Established by named, reproducible evidence quoted in the Evidence column. |
| **UNKNOWN** | Not established. The Evidence column states the exact artefact that would establish it. |
| **AUDIT INPUT** | A value the owner supplied as a starting point for configuration. It is **not** evidence, it does not make any fact VERIFIED, and it may be wrong. |
| **NOT MODELLED** | V1 deliberately models no such effect. This is a declared limitation, not a claim that the effect is zero. |
| **NOT ENFORCED** | V1 models the arithmetic but enforces no broker-side constraint on it. A declared limitation, not a claim the constraint is absent. |
| **NOT APPLICABLE** | The effect does not exist for this instrument class. |

### 1.1 Two classes of fact, and the closure rule

Every row in §3 and §4 carries a **class**, and the class — not the status —
decides whether it can block closure.

| Class | Meaning |
|---|---|
| **CR — closure-required** | A fact that must be true for V1's *implemented* money and execution semantics to be true. It must reach **VERIFIED with named evidence** before any authoritative money/execution claim is permitted. |
| **NB — non-blocking declared limitation** | An effect V1 deliberately does not model, or a constraint V1 deliberately does not enforce, or a column V1 deliberately does not read. It stays visible as a limitation (§7) forever and **never blocks closure**. |

The mechanical test for CR is one question: *does V1's implemented calculation
consume this fact?* Slippage, financing, latency and corporate actions are not
consumed by anything, so they are NB. Pip size is consumed by every money
number, so it is CR.

**Closure rule (mechanically satisfiable).** The audit is **CLOSED** when all
three of the following hold, and not before:

1. **every CR fact in §3 and §4 is VERIFIED** with named, reproducible
   evidence recorded in its Evidence column;
2. **every NB item is explicitly classified** NOT MODELLED / NOT ENFORCED /
   NOT APPLICABLE and its consequence appears in §7; and
3. **the verified execution-cost binding exists in the repository** — the
   `VERIFIED_COST_BINDING` constant of §3.0, carrying a verified
   `execution_price_model` — created by the same commit that flips this
   header to CLOSED.

Closure does **not** claim the NB effects are zero. It claims exactly this:
every fact the implemented arithmetic depends on has been established, and
everything the platform does not model is written down. NB limitations
continue to accompany every quoted number after closure.

Until closure the audit is OPEN: every run that produces money-denominated
Plane-B output declares `cost_semantics: unverified`, is permanently marked in
its `RunManifest`, and its money numbers may not be represented as
authoritative real-money estimates
([ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md) Amendment 1).

**What an OPEN audit does *not* block.** Implementing the simulator, proving it
against hand-computed synthetic fixtures, running the harness-inversion
canaries, and exercising the end-to-end Plane-B loop. Those establish that the
arithmetic is correct given the configured inputs — a separate question from
whether the inputs are true. A synthetic fixture may additionally *declare* its
own price-side semantics (`execution_price_model = synthetic_declared`, §3.0),
because a fixture defines its instrument; that declaration verifies the
arithmetic and asserts nothing about the real dataset.

## 2. Which broker's documentation is required

The dataset's clock domain is `mt5.justmarkets-demo3.wallclock`
([V1.md](../architecture/V1.md) §3), i.e. the rows originate from a
**MetaTrader 5 JustMarkets demo account**. The authoritative evidence for §3
and §4 is therefore:

1. the **MT5 symbol specification dialog** for `XAUUSD` on that exact account
   (contract size, tick/point size, digits, minimum/step/maximum volume, stops
   level, freeze level, swap rates, profit/margin currency), captured as a
   screenshot or an exported symbol-properties listing; and
2. the broker's **published account-type / commission schedule** for the
   account type the demo mirrors, including whether commission is charged per
   side or per round turn, in which currency, and at what volume tiers.

Both are external to this repository and cannot be derived from the dataset.
A demo account's parameters may differ from a live account's; if a live account
is ever the target, the audit must be re-opened against that account's
specification.

## 3. Instrument and price facts

### 3.0 What is bound, what is unbound, and how VERIFIED is enforced

Two different things have been confused in cost work generally, and were
confused in the first version of this record:

| | |
|---|---|
| **Executable price path** | *Which* prices a LONG or SHORT entry and exit actually transact at, and therefore which price series the take-profit and stop-loss levels must be compared against. |
| **Monetary cost arithmetic** | *How much money* is charged per trade for spread and commission. |

V1 binds the second completely and the first only **provisionally**, under a
name, because the first depends on A6 (price side) and B3 (spread symmetry),
which are UNKNOWN.

**Provisional binding in force (V1):** `execution_price_model =
recorded_path_unverified`. Entry fills, exit fills and both bracket levels are
read from the recorded OHLC series exactly as stored, and the price side of
that series is UNKNOWN. If the series turns out to be one-sided — say Bid —
then a long entry executes on the Ask side, the executable entry price differs
from the recorded `open` by about the spread, and **every level derived from
that entry is displaced by the same amount**. Subtracting spread as money
afterwards does not repair the trigger *timing*. That is why this model is
legal only while `cost_semantics = unverified`.

**Explicitly UNBOUND until A6 and B3 are VERIFIED:** the mapping from the
recorded series to the executable series per direction, and therefore whether
bracket levels are derived from the recorded `open` or from a side-adjusted
executable entry price. **No repository document may state that the recorded
price path is the executable price path as a settled fact.** The verified model
value, and its exact executable-price rules, are created by the ADR amendment
that closes this audit — not guessed now.

**The fail-closed binding (enforces RM-3).** `core` holds one frozen module
constant:

```
VERIFIED_COST_BINDING: ExecutionCostBinding | None = None   # None while this audit is OPEN
```

- `cost_semantics = VERIFIED` is constructible **only** when the constant is not
  `None` and the spec's `cost_binding` equals it field-for-field, including a
  `binding_digest` the platform **recomputes** from the binding's own canonical
  document. Anything else — a config field, a CLI argument, an edited run
  record — is refused at config resolution
  ([PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md) §14 R10–R13).
- The constant is created by exactly one commit: the one that closes this audit
  (§8). Creating or changing it is an **architecture-level change** under
  `CLAUDE.md` change control — an ADR and review — never a configuration act.
- Because the binding id and digest enter `experiment_fingerprint`, closure
  produces **new** run identities. Runs recorded under an OPEN audit keep
  `cost_semantics: unverified` permanently and are **never** retroactively
  upgraded.

The `spread_pips`, `commission_*`, unit-conversion and price-model fields
therefore live **inside** `ExecutionCostBinding`, not loose in `ExecutionSpec`:
their meaning is contingent on the facts below, and grouping them under one
identified, digest-bearing binding is what lets the verified semantics attach
later without any component having claimed them early
([ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) §8).

### 3.1 Instrument and price facts

| # | Fact | Class | Status | Value | Evidence — held, or exactly what is required |
|---|---|---|---|---|---|
| A1 | Point/pip size for `XAUUSD` in price units | **CR** | **UNKNOWN** | — | MT5 symbol specification: `SYMBOL_POINT` and `digits`. **Also required: the definition of "pip" the owner's `20 pips` input uses**, since for XAUUSD "pip" is used inconsistently for both `0.01` and `0.10` price units — a factor-of-10 error in every cost number. |
| A2 | Contract size — base units per 1.00 lot | **CR** | **UNKNOWN** | — | MT5 symbol specification: `SYMBOL_TRADE_CONTRACT_SIZE` (commonly 100 oz for XAUUSD, **not assumed here**). Converts price movement into money. |
| A3 | Quote currency and account currency, and the conversion between them | **CR** | **UNKNOWN** | — | MT5 symbol specification profit currency, plus the account's deposit currency. If they differ, whether a fixed or time-varying conversion applies. |
| A4 | Minimum volume, volume step, maximum volume | **NB** | **UNKNOWN / NOT ENFORCED** | — | MT5 symbol specification: `SYMBOL_VOLUME_MIN` / `_STEP` / `_MAX`. V1 enforces no lot-step constraint (§7 L4); nothing in the implemented arithmetic consumes it. |
| A5 | Minimum stop distance (`stops level`) and freeze level | **NB** | **UNKNOWN / NOT ENFORCED** | — | MT5 symbol specification: `SYMBOL_TRADE_STOPS_LEVEL`, `SYMBOL_TRADE_FREEZE_LEVEL`. V1 enforces no minimum stop distance (§7 L4). |
| A6 | **Price side of the OHLC columns — bid, ask, or mid** | **CR** | **UNKNOWN** | — | MT5 exporter behaviour for `CopyRates` on this account, plus the broker's statement of which side its M1 bars record. **This fact binds the executable price path (§3.0)**: it decides whether a long entry transacts at the recorded `open` and therefore where the bracket levels sit. |
| A7 | Tick size / price precision | **NB** | **UNKNOWN / NOT MODELLED** | — | MT5 symbol specification `SYMBOL_TRADE_TICK_SIZE` and `digits`. V1 rounds no price to a tick grid; the dataset can *bound* this (§5 probe P-1) but cannot establish it. |

## 4. Cost facts

| # | Fact | Class | Status | Value | Evidence — held, or exactly what is required |
|---|---|---|---|---|---|
| B1 | Units of the dataset `spread` column | **NB** | **UNKNOWN / NOT MODELLED** | — | Broker/exporter statement that MT5's `spread` field is in **points** for this symbol, combined with A1. The column is `int32`, which is consistent with points and inconsistent with a price-unit float, but consistency is not proof. **NB because no component reads the column** (E5, §7 L5); it becomes **CR** the moment any component does. |
| B2 | Sampling semantics of the `spread` column within a bar | **NB** | **UNKNOWN / NOT MODELLED** | — | Whether the exported per-bar value is the spread at the bar open, at the close, the bar maximum, or an average. Until this is VERIFIED the column is **not used by the simulator at all**; same conditional promotion to CR as B1. |
| B3 | Whether the spread is symmetric about mid | **CR** | **UNKNOWN** | — | Depends on A6. Determines whether half-spread-per-side is a valid decomposition and therefore **how the executable price path is bound** (§3.0). |
| B4 | Spread magnitude charged per round trip | **CR** | **AUDIT INPUT** | `20 pips` (owner-provided, 2026-08-14) | An audit input is **not evidence**. Closure requires the broker/account statement of the effective spread for this symbol and account type; its money value is undetermined until A1, A2 and A3 are VERIFIED, and whether one constant fairly represents a time-varying spread must be stated explicitly at closure. |
| C1 | Commission magnitude | **CR** | **AUDIT INPUT** | `$0.06 per 0.01 lot` → `$6.00 per 1.00 lot` (owner-provided, 2026-08-14) | Not evidence. Requires the broker's published commission schedule for the account type. |
| C2 | Commission basis — per side or per round turn | **CR** | **UNKNOWN** | — | Broker commission schedule. This is a **factor-of-two** error in every net PnL number if guessed wrong, so the binding requires it to be declared explicitly and never defaults it. |
| C3 | Commission currency, and conversion to the account currency | **CR** | **UNKNOWN** | — | Broker commission schedule plus A3. |
| C4 | Whether commission varies by volume tier, account type or promotion | **CR** | **UNKNOWN** | — | Broker commission schedule. Closure requires knowing *whether* it varies; if it does, the single configured constant is recorded as a declared approximation at closure. |
| D1 | Slippage characteristics (mean, dispersion, asymmetry, dependence on volatility or on order type) | **NB** | **UNKNOWN / NOT MODELLED** | — | Execution reports from a real account. V1 models **no** slippage and builds no slippage framework; results are therefore optimistic by an unmeasured amount, and this is stated wherever they are reported. |
| D2 | Swap / financing / overnight carry | **NB** | **UNKNOWN / NOT MODELLED** | — | MT5 symbol specification swap long/short, swap mode and triple-swap day. V1 models **no** financing. For a strategy that holds positions across day boundaries this is a material omission, not a rounding error. |
| D3 | Execution latency, requotes, rejections | **NB** | **UNKNOWN / NOT MODELLED** | — | Real-account execution statistics. V1 assumes every eligible fill occurs. |
| D4 | Dividends / corporate actions | **NB** | **NOT APPLICABLE** | — | Spot metal CFD; no corporate actions. |

**Closure-required set, in full:** A1 · A2 · A3 · A6 · B3 · B4 · C1 · C2 · C3 ·
C4. Everything else in §3 and §4 is NB and is carried as a declared limitation
in §7. This list is exactly the set of facts V1's implemented money and
execution semantics consume (§1.1), and every one of them is obtainable from
the two artefacts named in §2.

## 5. Facts already established inside this repository

These are structural facts about the dataset, verified from the repository and
its manifests. **None of them establishes a cost semantic** — they establish
only what the data physically is, and which questions remain open.

| # | Fact | Status | Evidence |
|---|---|---|---|
| E1 | The curated dataset carries a per-bar `spread` column typed `int32` | **VERIFIED** | `src/trading_research/data/adapters/mt5.py` physical column table; `src/trading_research/data/manifests/adapter_columns.py` `_MT5_EXPORTER_V1_DTYPES`; pinned by `tests/test_adapter_dtype_conformance.py` |
| E2 | `open`, `high`, `low`, `close` are `float64`; `tick_volume` and `real_volume` are `int64` | **VERIFIED** | same sources as E1 |
| E3 | The time axis is broker wall-clock (`mt5.justmarkets-demo3.wallclock`, seconds), never UTC | **VERIFIED** | [ADR-0002](../adr/ADR-0002-native-time-model.md); [V1.md](../architecture/V1.md) §3 |
| E4 | Instrument and timeframe are XAUUSD M1, sourced from MT5 | **VERIFIED** | dataset identities `XAUUSD_S_M1_MT5_V1` / `XAUUSD_S_M1_CURATED_V1` and their manifest administrative metadata |
| E5 | No component currently interprets the `spread` column, and none may until B1/B2 are VERIFIED | **VERIFIED** | absence of any reader; architecture guards listed in [PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md) §26 |

### Probe P-1 — permitted, bounded, and insufficient on its own

A distributional summary of the `spread` column over the **published TRAIN
view only** (min, max, median, mode, value counts) is permitted: TRAIN is
non-gated, requires no ceremony and produces no audit event. It may **refute**
a unit hypothesis (a column whose values cluster near 20 is inconsistent with
a price-unit interpretation of `20 pips` at a `0.01` pip size) but it can never
**establish** one, because any set of integers is consistent with several
(unit, pip-size) pairs. Probe P-1 is therefore a supporting check, never
closure evidence, and it must not be run on OOS, LOCKBOX, SNAPSHOT, RAW or
CURATED identities.

## 6. Configuration used while the audit is OPEN

The values below are what the Phase-4 `ExecutionSpec` is configured with for
XAUUSD experiments today. They are **audit inputs, not verified semantics**;
every one of them is declared explicitly in the resolved config, enters the
`experiment_fingerprint`, and is reproduced in the run record.

| Field | Where it lives | Configured value | Backing status |
|---|---|---|---|
| `lot_size` | `ExecutionSpec` | owner-chosen per experiment (fixed) | configuration |
| `take_profit_pips` / `stop_loss_pips` | `ExecutionSpec` | owner-chosen per experiment | configuration |
| `binding_id` | `ExecutionCostBinding` | `"unverified-audit-input-v1"` | this document |
| `binding_digest` | `ExecutionCostBinding` | `None` (no verified binding exists) | §3.0 |
| `cost_semantics` | `ExecutionCostBinding` | `unverified` — **the only legal value today** (§3.0) | this document |
| `execution_price_model` | `ExecutionCostBinding` | `recorded_path_unverified` | **UNBOUND until A6/B3 (§3.0)** |
| `contract_size` | `ExecutionCostBinding` | must be declared explicitly | **UNKNOWN (A2)** |
| `price_units_per_pip` | `ExecutionCostBinding` | must be declared explicitly | **UNKNOWN (A1)** |
| `account_currency_per_quote_currency` | `ExecutionCostBinding` | must be declared explicitly | **UNKNOWN (A3)** |
| `spread_pips` | `ExecutionCostBinding` | `20.0` | **AUDIT INPUT (B4)** |
| `commission_per_lot` | `ExecutionCostBinding` | `6.00` | **AUDIT INPUT (C1)** |
| `commission_basis` | `ExecutionCostBinding` | must be declared explicitly (`per_side` \| `per_round_turn`) | **UNKNOWN (C2)** |

No field has a default. A missing field is a configuration refusal, never a
silently assumed zero. The contingent fields sit inside `ExecutionCostBinding`
rather than loose in `ExecutionSpec` precisely so that this table has one
subject and the verified semantics can attach to it as a unit (§3.0).

## 7. Declared limitations while the audit is OPEN

| # | Limitation |
|---|---|
| L1 | Money figures are **conditional on the audit inputs above**. A wrong pip size, contract size or commission basis rescales or halves every result. |
| L2 | **The executable price path is bound only provisionally.** Under `execution_price_model = recorded_path_unverified` (§3.0), fills and both bracket levels are read from the recorded series, whose price side is UNKNOWN (A6). If that series is one-sided, the executable entry price and every level derived from it are displaced by up to the spread for one direction, which changes **trigger timing**, not just accounting — charging spread as money afterwards does not repair it. The side-dependent interpretation is therefore **explicitly UNBOUND**, and no result produced under this model may be presented as a verified execution outcome. |
| L2a | Under the current provisional model, spread is charged **only** as money and moves no price. When a verified side-bound model replaces it, the executable-price offset and the money charge must be re-derived together by the closing ADR amendment, or the spread would be counted twice. Recorded now so the trap is not rediscovered at closure. |
| L3 | **No slippage (D1), no financing/swap (D2), no latency or rejection (D3)** is modelled. Results are optimistic by an unmeasured amount. |
| L4 | Broker **minimum stop distance and lot-step constraints are not enforced** (A4, A5). A configured take-profit or stop-loss that no broker would accept will still simulate. |
| L5 | The per-bar `spread` column is **not used**; a single configured constant stands in for a time-varying quantity (B2, B4). |
| L6 | Intrabar path is unobservable. Where both bracket levels fall inside one bar the stop loss is taken ([ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) §5) and the trade is flagged; a result dominated by such bars is dominated by an assumption, which is why the ambiguity rate is reported. |

## 8. Closure procedure

1. Obtain the two external artefacts named in §2.
2. Fill in **every closure-required (CR) row** in §3 and §4 with its value and
   the exact evidence (document title, date, account type, and where the
   artefact is stored), flipping the row to **VERIFIED**. NB rows are *not*
   filled in; they are re-confirmed as declared limitations in §7.
3. **Bind the executable price path.** From the verified A6 and B3, write the
   exact executable-price rules — entry price per direction, exit price per
   direction, and how bracket levels are derived from the executable entry —
   as an amendment to
   [ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) §8, adding the
   corresponding `ExecutionPriceModel` value. Re-derive the spread money charge
   in the same amendment so the spread is not counted twice (§7 L2a).
4. **Create the verified binding.** In the same commit, set `core`'s
   `VERIFIED_COST_BINDING` constant to the frozen `ExecutionCostBinding` built
   from the verified facts, with its `binding_id`, its verified
   `execution_price_model` and its recomputed `binding_digest`; record that id
   and digest here. Until this constant exists, `cost_semantics: verified` is
   unreachable by construction (§3.0).
5. Re-derive the §6 configuration from the verified facts; where a verified
   value differs from an audit input, say so explicitly and re-run affected
   experiments. **Never retro-label old results:** runs made under the OPEN
   audit keep `cost_semantics: unverified` permanently, and the new binding
   identity gives the re-runs a different `experiment_fingerprint`.
6. Set the header status to **CLOSED**, record the date, and move
   `ExecutionSpec` configurations to the verified binding.
7. Update [PROJECT_STATE.md](../../PROJECT_STATE.md), the roadmap Phase-4 entry,
   and [ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md) with the closure
   date. This is checkpoint **CP-13C**
   ([PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md) §16) — a
   documentation act with a recorded date, external to Phase-4 completion.
8. Any later change of broker, account type or instrument **re-opens** this
   audit: reset the constant to `None` in the same commit that re-opens the
   header. A closed audit is closed for one named account and symbol only.

## 9. Related

[ADR-0020](../adr/ADR-0020-cost-semantics-quarantine.md) (the gate) ·
[ADR-0023](../adr/ADR-0023-phase-4-simulation-contracts.md) (what consumes these
facts) · [PHASE4_SIMULATION.md](../phases/PHASE4_SIMULATION.md) (the plan) ·
[RESEARCH_POLICY.md](RESEARCH_POLICY.md) §7 (why the simulator is second, not
first)
