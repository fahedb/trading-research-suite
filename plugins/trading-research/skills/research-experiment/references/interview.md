# Resolve the experiment, not just its parameters

Use this during intake and when a newly discovered implementation issue changes
the meaning of the experiment. Do not read it to the user as a questionnaire.
First extract answered items, then ask the highest-impact remaining questions.

## What is being tested?

- State the research question and a falsifiable hypothesis. A descriptive study
  may instead specify the distribution, pattern or diagnostic it will measure.
- Establish whether the user wants predictive evidence, a trade simulation, or
  both in sequence. Explain the distinction without making the user learn the
  internal names of pipeline stages.
- Agree on a baseline, metric, interpretation criterion and stopping rule before
  inspecting results. A favorable PnL alone is not evidence of predictive edge.
- Bound the run count and resource use. Record any predetermined variants as
  separate trials; do not silently turn one experiment into an optimization loop.

## What is known at the decision point?

- Instrument, observed timeframe, research period and clock semantics come from
  user instructions and admitted metadata. Never infer UTC from integer keys or
  a filename. Do not ask the user to supply metadata already available locally.
- Define the indicator formula, inputs, period, smoothing/initialization,
  warm-up, finite history, gap handling and when its value becomes observable.
  “RSI 14” or “crosses the average” can still contain material ambiguities.
- Define crossing versus being above/below, equality/ties, repeated signals,
  conflicting conditions and whether an unfinished bar can participate.
- For multi-timeframe rules, pin bucket alignment and closed-bar availability.
  A pivot confirmed five bars later cannot be used on its original pivot bar.
- Define outcome, horizon and reference observation; future outcomes belong to
  the platform's outcome channel, not to the feature/model input.

## If the request includes simulated trades

Clarify entry, exit, sizing, simultaneous positions, next executable observation,
same-bar conflicts and costs. If the supplied indicator defines entries only,
ask about exits instead of inventing them. Capture both stop/target rules and
what happens at a research fold boundary. Specify provisional cost assumptions
honestly; the platform currently has no verified real-money cost binding.

Default research methodology establishes Plane-A evidence before Plane B. The
original Gaps exception does not authorize a new exception. If the user explicitly
requests a simulation first, explain the limitation and record that instruction
for this experiment. Never silently change their request to another experiment.

## Data and feasibility

Identify required source fields, available history, missing/duplicate observations
and provenance. The initial specification can be frozen with an identified data
acquisition task; a run needs an admitted immutable identity and enough context.

Separate:

- **Unanswered meaning:** “What is the exit?” blocks freezing the strategy.
- **Missing implementation:** a precisely defined indicator can be implemented
  after the design is frozen.
- **Missing observation:** a source must supply it; an indicator cannot recover
  unobserved prices or genuine trade volume from tick counts.
- **Unsupported architecture:** a cross-clock news join, for example, needs an
  architecture decision. Do not quietly approximate it with available prices.

## Conversation examples

“Buy when RSI drops under 30” calls for a small first question batch: instrument
and timeframe if unknown; a crossing or continuous condition; intended exit.
Do not ask for host paths, hashes and broker timestamps in the first reply when
the user is still defining the idea. Check available metadata independently.

“Check whether high volatility predicts the next 20-bar return” is predictive:
clarify volatility definition, baseline, data and metric. Do not require a stop
loss, lot size or simulated trade merely because the product can simulate them.

“Use my H1 file, fill missing prices and test it” needs a missing-data decision.
Explain that invented candles change the evidence. Preserve observed gaps or
obtain the missing source observations; synthetic fixtures are for tests only.

“I already approved revision 1; finish implementing it” means retrieve that
revision, verify its digest and continue within its scope. Do not restart the
interview or demand the same confirmation again.
