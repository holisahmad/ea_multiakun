# Smart Trade Multi Account

## Product Brief, Trading Rules, Architecture, and AI Agent Implementation Guide

**Document status:** Initial specification / proposal\
**Version:** 0.1\
**Product name:** Smart Trade Multi Account (STMA)\
**Primary platform:** MetaTrader 5\
**EA language:** MQL5\
**Controller language:** Python\
**Initial deployment:** Local computer, three MT5 terminals, demo
accounts first\
**Audience:** AI coding agents, system analysts, MQL5 developers, Python
developers, QA engineers

------------------------------------------------------------------------

## 1. Purpose

Build a configurable multi-account trading system that coordinates up to
three MetaTrader 5 accounts. A primary account may enter on a valid
trading signal. If price moves against the initial position, secondary
accounts may enter at later stages only when configurable
price-distance, support/strategy confirmation, and risk conditions are
all satisfied.

The system must prioritize **risk controls, correct execution,
recoverability, and observability** over maximizing trade frequency.

This document is a starting specification. It is not proof that the
strategy is profitable and must not be treated as financial advice. No
real-money trading is permitted until the required tests and explicit
operator approval are complete.

## 2. Core Concept

Example roles:

-   **Account 1 --- Primary:** takes the initial signal-based entry.
-   **Account 2 --- Secondary:** may enter after the configured adverse
    price movement and valid entry confirmation.
-   **Account 3 --- Tertiary:** may enter after a further adverse
    movement and valid entry confirmation.

Each account has its own MT5 terminal and account-specific executor EA.
A central controller coordinates the strategy and evaluates aggregate
risk.

### Important risk principle

Three accounts trading the same instrument and direction remain exposed
to the same market movement. Splitting positions across accounts does
not diversify away market risk. The strategy can suffer simultaneous
losses in all accounts, particularly during a persistent trend or a
support breakdown.

Do not implement unlimited averaging, unlimited cycles, or automatic
re-entry merely because previous positions are losing.

## 3. Initial Scope

### In scope

-   Up to three MT5 trading accounts.
-   Signal-driven initial entry.
-   Staged escalation across accounts.
-   Configurable lot size, layer limits, entry distance, TP, SL, and
    trailing stop.
-   Individual-position TP mode and optional group-level exit mode.
-   Per-account and aggregate risk controls.
-   Central controller and account executor EAs.
-   Position reconciliation after restart or communication failures.
-   Local dashboard and logs.
-   Demo testing, backtesting support, and fault-injection testing.
-   No broker-specific API required when the broker supports trading
    through MT5.

### Out of scope for the first release

-   Public multi-tenant SaaS.
-   Copy trading for unrelated customers.
-   Guaranteed-profit claims or autonomous strategy optimization for
    live funds.
-   Direct broker-server integration that bypasses MT5.
-   Automatic changes to risk limits without operator approval.
-   Multiple overlapping strategy cycles per instrument.
-   Unbounded martingale or recovery sizing.
-   Real-money deployment before acceptance criteria are passed.

## 4. Recommended Architecture

``` text
                 SMART TRADE MULTI ACCOUNT
                             |
                             v
                   CENTRAL CONTROLLER
          +------------------+------------------+
          |                  |                  |
          v                  v                  v
    Account Executor 1  Account Executor 2  Account Executor 3
       MQL5 / MT5          MQL5 / MT5          MQL5 / MT5
          |                  |                  |
          v                  v                  v
      Broker MT5         Broker MT5         Broker MT5
       account 1          account 2          account 3
```

### 4.1 Central Controller (Python)

Responsibilities:

-   Receive and validate strategy signals.
-   Maintain strategy-cycle state.
-   Evaluate stage eligibility.
-   Calculate aggregate exposure and risk.
-   Issue idempotent execution commands.
-   Track acknowledgements and execution results.
-   Reconcile controller state with actual MT5 positions.
-   Persist configuration, commands, events, and cycle history.
-   Provide local dashboard/API.
-   Send optional notifications.
-   Stop new entries when risk or system-health rules trigger.

The controller must not assume that a command was executed just because
it was sent.

### 4.2 Account Executor EA (MQL5)

One EA instance per account/terminal.

Responsibilities:

-   Report connection and account status.
-   Report positions and relevant pending orders.
-   Validate commands and account identity.
-   Validate symbol, volume, margin, spread, and trade permissions.
-   Execute permitted orders.
-   Apply/manage TP, SL, and trailing logic according to configuration.
-   Return execution result and broker retcodes.
-   Prevent duplicate execution of the same command.
-   Reconcile actual positions after restart.
-   Reject stale, malformed, unauthorized, or risk-invalid commands.

The EA is the execution authority for its own terminal. The controller
coordinates; it does not directly send orders to a broker.

### 4.3 Communication

Start with a local, authenticated communication channel. Candidate
approaches:

1.  Local HTTP service with `WebRequest` from MQL5.
2.  Local TCP socket where supported and implemented safely.
3.  Carefully designed local IPC/file exchange for a minimal prototype.

Choose one method after a small proof of concept. Do not implement
multiple transports in the first release.

If using `WebRequest`, document the MT5 URL allowlist configuration and
the fact that it cannot be exercised directly in the MT5 Strategy
Tester. Test communication separately from strategy backtests.

Use timeouts, request IDs, authentication for local commands, schema
validation, and explicit error handling. Never expose a trading-control
endpoint to the public internet without a separate security design.

## 5. Strategy Rules

### 5.1 Entry stage 1 --- Primary account

1.  Receive a signal.
2.  Validate signal age, direction, instrument, and strategy identifier.
3.  Confirm that the strategy is enabled and no disallowed cycle is
    active.
4.  Check market data freshness, spread, trading session rules if
    configured, and account health.
5.  Evaluate per-account and aggregate risk.
6.  Validate volume and margin.
7.  Create a unique cycle and command ID.
8.  Submit the order through Account 1 EA.
9.  Confirm the actual execution result and reconcile the resulting
    position.
10. Manage the position according to configured exit rules.

A valid signal is not an unconditional instruction to trade. Risk Engine
approval is mandatory.

### 5.2 Entry stage 2 --- Secondary account

Account 2 becomes **eligible for evaluation**, not automatically
eligible to trade, when the configured adverse price-distance threshold
is reached.

Before entry, validate all of the following:

-   The cycle is active and eligible for stage 2.
-   The signal/cycle has not expired or been invalidated.
-   Price-distance condition is satisfied.
-   The configured support/strategy confirmation is satisfied when
    enabled.
-   Spread and market-data checks pass.
-   Account 2 is connected and its state is current.
-   The layer limit has not been reached.
-   Aggregate exposure and loss limits permit the entry.
-   The same stage/command has not already executed.
-   The order volume is valid for the account and symbol.

If any required check fails, do not enter. Record the reason and wait or
transition to protection according to policy.

### 5.3 Entry stage 3 --- Tertiary account

Apply the same validation principles as stage 2, using a separately
configured threshold/zone and stage-3 rules. The third account is not a
license to keep averaging after the strategy has reached its maximum
escalation stage.

If price continues moving adversely after stage 3, do not automatically
create a new cycle. Follow the aggregate-risk and protection policy.

### 5.4 Entry model

The recommended initial model is **hybrid**:

-   A configurable minimum adverse distance must be reached.
-   A configured entry confirmation must also pass.
-   Risk Engine approval must pass.

Distance alone must not trigger an order by default. Support/resistance
is not guaranteed to hold. Support detection must be implemented as a
separate, testable strategy module and must not be faked with arbitrary
hard-coded levels.

Initial release may use a configurable fixed price distance. ATR-based
distance and automated support-zone detection should be added only after
the baseline is tested.

### 5.5 Direction

The system must support signal-based direction in its domain model, but
the first tested strategy can be BUY-only if that is the agreed initial
scope. Do not silently assume SELL behavior is identical; validate
short-selling rules, symbol properties, and stage logic separately.

## 6. Layers and Position Limits

Initial defaults:

-   Default volume: `0.01` lots per entry.
-   Maximum layers per account: `3`.
-   Maximum active cycles per instrument: `1`.
-   Maximum accounts: `3`.
-   Maximum active positions in one cycle: initially bounded by
    configured layer limits, normally no more than 9 across three
    accounts.

All values must be configurable and validated against broker symbol
specifications.

Distinguish these concepts:

-   `lot_per_entry`: volume for one new position.
-   `max_layers_per_account`: maximum positions/layers for that account
    in the cycle.
-   `max_volume_per_account`: total open volume allowed on one account.
-   `max_volume_strategy`: aggregate open volume across the cycle.
-   `max_positions_strategy`: total active positions allowed.
-   `max_active_cycles`: number of simultaneously active cycles.

A repeated signal must not bypass these limits. Do not interpret every
incoming signal as permission to add another layer.

The system must account for broker differences in symbol names,
minimum/maximum volume, volume step, tick size, tick value, contract
size, margin requirements, and account currency.

## 7. TP, SL, and Trailing Stop

Support configurable exit modes.

### 7.1 Individual TP (recommended prototype default)

Each position has its own TP. A position can close independently without
waiting for other accounts.

### 7.2 Group TP (later feature)

Calculate net cycle P/L across all relevant positions and, if the
configured target is reached, request closure of the remaining positions
in that cycle.

Net P/L calculations should account for realized P/L, current floating
P/L, commissions, swaps, and other available costs. Be explicit about
whether already-realized profits are included in the cycle target.

Cross-account closure is not atomic. Some accounts may close while
another fails. Do not mark the cycle complete until actual positions
have been reconciled and all required close attempts have reached a
known outcome.

### 7.3 SL policy

Default: individual SL enabled.

An optional mode may disable individual SL only if aggregate protection
remains enabled. The system must never allow all protection limits to be
disabled accidentally.

Mandatory protections:

-   Aggregate loss/drawdown limit.
-   Per-account risk limits.
-   Maximum exposure/volume.
-   Margin safety limits.
-   No-new-entry protection mode.
-   Emergency handling for stale/disconnected account data.

A software/terminal-managed stop cannot guarantee a specific fill price
during gaps, slippage, market closure, or connectivity failure. Prefer
server-side SL when supported and appropriate.

### 7.4 Trailing stop

Configurable fields:

-   `trailing_enabled`
-   `trailing_start`
-   `trailing_distance`
-   `trailing_step`

Define units explicitly. Never mix raw price distance, MT5 points, pips,
and ticks. Validate stop/freeze levels and broker rules before modifying
a stop.

## 8. Risk Engine

Risk Engine approval is mandatory before every new entry.

### 8.1 Per-position controls

-   Valid volume and symbol.
-   Spread limit.
-   Allowed slippage/deviation where applicable.
-   TP/SL policy.
-   Order and market-data validity.
-   Estimated loss at stop when an SL is used.

### 8.2 Per-account controls

-   Maximum open positions/layers.
-   Maximum open volume.
-   Maximum floating loss.
-   Maximum drawdown.
-   Minimum free-margin or margin-level threshold.
-   Connection/data freshness requirement.
-   Maximum order failures within a configured window.

### 8.3 Aggregate controls

-   Total floating P/L.
-   Realized P/L for the current cycle.
-   Combined equity/drawdown where values can be normalized reliably.
-   Total volume and positions.
-   Aggregate margin/exposure where comparable.
-   Estimated loss under configured adverse-price scenarios.
-   Stage-3 and total-cycle exposure limits.

Do not add balances in different currencies or account denominations
without normalization. A cent account's displayed balance may use a
different denomination from a standard USD account. Determine the actual
deposit currency and units from the terminal, and normalize values
before combining them.

Aggregate-risk calculation must document its assumptions and
limitations. If an account's current state is unknown or stale, block
new entries until reconciliation succeeds.

### 8.4 Protection behavior

When a risk limit is reached:

1.  Block new entries globally or for the affected account according to
    policy.
2.  Persist the trigger and current state.
3.  Continue monitoring existing positions.
4.  Apply the configured position-management policy.
5.  Notify the operator.
6.  Require explicit operator authorization before resuming after a
    critical trigger, unless a separately tested safe auto-resume policy
    is defined.

Do not automatically close all positions merely because the controller
has lost connectivity. First determine what the EA and broker are
actually doing. The policy for unknown state must be conservative and
explicit.

## 9. State Machine

Suggested cycle states:

-   `IDLE`
-   `SIGNAL_VALIDATED`
-   `ACCOUNT_1_PENDING`
-   `ACCOUNT_1_ACTIVE`
-   `ACCOUNT_2_ELIGIBLE`
-   `ACCOUNT_2_PENDING`
-   `ACCOUNT_2_ACTIVE`
-   `ACCOUNT_3_ELIGIBLE`
-   `ACCOUNT_3_PENDING`
-   `ACCOUNT_3_ACTIVE`
-   `RECOVERY`
-   `PROTECTION`
-   `CLOSING`
-   `RECONCILIATION`
-   `COMPLETED`
-   `FAILED_REQUIRES_OPERATOR`

State transitions must be explicit and tested. Persist state transitions
before/after consequential operations as appropriate. Do not infer state
solely from in-memory variables.

`ELIGIBLE` means the stage can be evaluated; it does not mean an order
must be placed.

A cycle cannot transition to `COMPLETED` until the controller reconciles
actual positions and confirms that no positions managed by that cycle
remain open, or records an explicit unresolved/manual state.

## 10. Idempotency, Execution, and Reconciliation

Every command must have a unique immutable ID. Example:

``` json
{
  "strategy_id": "stma-xau-v1",
  "cycle_id": "cycle-unique-id",
  "account_id": "account-2-alias",
  "command_id": "unique-command-id",
  "action": "OPEN_BUY",
  "layer": 1,
  "symbol": "XAUUSD",
  "volume": 0.01,
  "created_at_utc": "ISO-8601 timestamp",
  "expires_at_utc": "ISO-8601 timestamp"
}
```

This is a schema example, not a live order instruction. Do not put
broker passwords, investor passwords, API secrets, or other credentials
in commands or logs.

Required behaviors:

-   Reject duplicate `command_id` values.
-   Reject expired commands.
-   Verify the configured account identity.
-   Record request, acknowledgement, broker result, and verified
    position state.
-   If the result is uncertain, reconcile before retrying.
-   Never blindly retry an order after a timeout.
-   Treat broker/terminal position data as the execution truth, not the
    controller's cached assumptions.
-   Handle manual trades and trades from other EAs according to an
    explicit policy.
-   Use a dedicated magic number and comments/tags where appropriate,
    but do not rely on comments alone as the only identity mechanism.

A successful HTTP response from an EA is not sufficient proof of a
filled order. Verify the trade result and actual position/order state.

## 11. Data Model

Use SQLite for the initial local prototype. A later server/multi-user
version may migrate to PostgreSQL.

Suggested entities/tables:

-   `accounts`: internal alias, role, terminal identity, enabled status;
    never store plain-text trading credentials.
-   `strategy_configs`: versioned strategy parameters.
-   `signals`: source, direction, symbol, timestamp, validity, payload.
-   `cycles`: strategy cycle, state, symbol, direction, start/end times.
-   `commands`: command ID, cycle ID, account alias, action, state,
    timestamps.
-   `executions`: broker/terminal result, retcode, order/deal/position
    identifiers where available.
-   `positions_snapshot`: observed positions and last-seen timestamp.
-   `risk_events`: risk checks and protection triggers.
-   `state_events`: auditable state transitions.
-   `system_events`: connectivity, errors, and recovery events.

Use migrations from the beginning. Use UTC timestamps in storage and
convert to local time only in the UI. Store monetary values with
suitable decimal-safe handling; avoid binary floating-point for
financial ledger totals where exact decimal arithmetic is required.

## 12. Configuration

Use validated configuration with safe defaults. Example only:

``` yaml
app:
  name: Smart Trade Multi Account
  environment: demo
  live_trading_enabled: false

strategy:
  strategy_id: stma-xau-v1
  symbol: XAUUSD
  direction_mode: signal_based
  default_lot: 0.01
  max_layers_per_account: 3
  max_active_cycles_per_symbol: 1
  entry_model: hybrid
  tp_mode: individual
  individual_sl_enabled: true
  trailing_enabled: false

risk:
  aggregate_protection_enabled: true
  max_strategy_positions: 9
  max_strategy_volume: null # MUST be configured and validated before trading
  max_account_drawdown_pct: null # MUST be configured before trading
  max_strategy_drawdown_pct: null # MUST be configured before trading
  minimum_margin_level_pct: null # MUST be configured according to account/broker
  block_entries_if_account_stale: true
  require_operator_resume_after_critical_trigger: true

accounts:
  - alias: account_1
    role: primary
    enabled: true
    max_layers: 3
    default_lot: 0.01
  - alias: account_2
    role: secondary
    enabled: true
    max_layers: 3
    default_lot: 0.01
  - alias: account_3
    role: tertiary
    enabled: true
    max_layers: 3
    default_lot: 0.01
```

Null risk values above are intentionally incomplete. The program must
refuse to enable live trading until all mandatory risk values have been
configured and validated. Do not invent risk thresholds or silently
substitute arbitrary defaults.

Never store terminal passwords in this YAML file. Use OS-protected
secrets or manual terminal login.

## 13. Dashboard Requirements

Initial dashboard should show:

-   Overall system state: demo/live, strategy enabled/paused, protection
    status.
-   Connection/data freshness for every account.
-   Account balance, equity, floating P/L, free margin, and
    currency/denomination.
-   Open positions and layers used.
-   Current strategy cycle and stage.
-   Aggregate P/L and risk metrics.
-   Recent commands, executions, and failures.
-   Risk/protection events.
-   Last successful reconciliation time.

Controls:

-   Start strategy.
-   Pause new entries.
-   Resume strategy.
-   Request close of strategy-managed positions.
-   Emergency protection.
-   Enable/disable account.
-   Demo/live indicator.

Require confirmation for consequential controls. The UI must clearly
distinguish pausing new entries from closing existing positions. A
failed close request must remain visible and must not be represented as
successful.

## 14. Notifications and Audit

Optional notifications (for example Telegram) may report:

-   New cycle started.
-   Position opened/closed.
-   Stage 2/3 evaluated, accepted, or rejected.
-   TP/SL/trailing event.
-   Risk limit triggered.
-   Account disconnected or stale.
-   Order rejected or result uncertain.
-   Reconciliation mismatch.
-   Manual intervention required.

Notifications must not contain passwords, access tokens, or other
secrets. Persist the underlying event even if notification delivery
fails.

## 15. Testing Strategy

### 15.1 Unit tests

Test at minimum:

-   Signal validation and expiry.
-   Stage eligibility.
-   Price-distance calculations and units.
-   Layer and position limits.
-   Risk-engine allow/deny outcomes.
-   Volume normalization.
-   TP/SL/trailing calculations.
-   Group P/L accounting.
-   State transitions.
-   Duplicate command handling.
-   Stale account data.
-   Unknown/partial execution outcomes.

### 15.2 Integration tests

Use MT5 demo terminals to test:

-   Controller-to-EA communication.
-   Account identity validation.
-   Order request/result handling.
-   Position snapshots and reconciliation.
-   Restart recovery.
-   Individual TP and SL behavior.
-   Trailing modifications.
-   Cross-account close requests.
-   Invalid symbol/volume and broker rejection paths.

### 15.3 Fault injection

Simulate:

-   Controller crash before/after order submission.
-   EA or terminal restart.
-   Network interruption.
-   Stale account data.
-   Timeout with unknown order outcome.
-   Duplicate or expired command.
-   Broker rejection.
-   One account closing while another fails.
-   Manual position changes.
-   Price jumping across multiple escalation thresholds.
-   Insufficient margin.
-   One account using a different symbol suffix or account currency.

### 15.4 Strategy validation

Backtest with realistic spreads, commissions, swaps, slippage
assumptions, and suitable historical data. Evaluate maximum drawdown,
profit factor, expectancy, tail losses, duration, stage-3 frequency, and
behavior during persistent one-directional trends.

Do not optimize only for net profit. Use out-of-sample periods and
forward testing. A profitable backtest does not guarantee future
profitability.

## 16. Acceptance Criteria Before Live Trading

Live trading must remain disabled until all applicable criteria pass:

1.  Duplicate commands do not create duplicate positions in tested retry
    scenarios.
2.  Position/layer/volume limits cannot be bypassed by repeated signals.
3.  Every strategy-managed position can be reconciled to an account and
    cycle.
4.  New entries are blocked when mandatory risk data is missing or
    stale.
5.  Aggregate and per-account risk limits are tested.
6.  Controller restart restores state from persistent storage and
    reconciles with MT5.
7.  Uncertain execution outcomes trigger reconciliation before retry.
8.  Partial cross-account closures are detected and reported.
9.  Invalid symbol, volume, margin, and disconnected-account cases fail
    safely.
10. Logs provide enough detail to reconstruct each command and state
    transition.
11. Demo forward testing meets project-defined performance and
    reliability thresholds.
12. An operator explicitly enables live trading after reviewing test
    results.

No performance threshold should be invented after seeing results. Define
thresholds before evaluating the final test set.

## 17. Implementation Roadmap

### Phase 0 --- Repository and environment audit

-   Inspect the existing repository before changing files.
-   Identify OS, Python version, MT5 availability, and existing code.
-   Produce a concise implementation plan and risk register.
-   Do not overwrite existing user work.

### Phase 1 --- Single-account EA prototype

-   Implement order validation, position tagging, TP/SL, trailing, and
    event logging.
-   Use demo only.
-   Add MQL5 tests or a documented reproducible test harness where
    native unit testing is limited.

### Phase 2 --- Controller and persistence

-   Add configuration validation, SQLite schema/migrations, cycle state
    machine, command IDs, and audit events.
-   Keep live trading disabled.

### Phase 3 --- One EA/controller integration

-   Implement communication and command acknowledgement.
-   Test timeout, duplicate command, restart, and reconciliation
    scenarios.

### Phase 4 --- Three-account orchestration

-   Add account registry, role assignment, account health, stage 2/3
    eligibility, and aggregate risk controls.
-   Test with three demo accounts/terminals.

### Phase 5 --- Dashboard and notifications

-   Add observability and operator controls.
-   Require confirmation for closing positions and changing critical
    settings.

### Phase 6 --- Validation and release

-   Run automated tests, demo forward testing, and fault injection.
-   Document limitations and unresolved risks.
-   Live mode remains disabled unless acceptance criteria pass and the
    operator explicitly approves.

## 18. AI Coding Agent Instructions

The agent must:

1.  Read this document completely before implementation.
2.  Inspect the existing repository, current branch, dependencies, and
    tests before modifying anything.
3.  Produce a short plan listing files to add/change, key decisions, and
    tests.
4.  Implement incrementally; do not generate the entire system in one
    unreviewable patch.
5.  Follow modular design and keep strategy logic, risk logic,
    execution, persistence, and UI separate.
6.  Treat Risk Engine approval as mandatory for every new order.
7.  Never enable live trading by default.
8.  Never invent broker specifications, account details, signals, risk
    thresholds, or test results.
9.  Use typed models/schemas and validate all external messages.
10. Add tests for every important business rule and every bug fix.
11. Avoid duplicate execution and blind retries.
12. Use structured logs with correlation IDs; redact secrets.
13. Keep credentials out of source control, logs, config examples, and
    screenshots.
14. Do not claim an MT5 order was filled until actual execution/position
    data confirms it.
15. Do not claim the strategy is profitable without reproducible test
    evidence.
16. Explain assumptions and unresolved questions briefly at the end of
    each milestone.
17. Run the available test suite and report exact results; do not claim
    tests passed if they were not run.
18. Stop and ask for a decision when a missing requirement affects risk,
    execution semantics, or irreversible behavior.
19. Do not add unrelated features or unnecessary dependencies.
20. Preserve existing user work and make changes in small, reviewable
    commits/patches when Git is available.

### Coding style

-   Prefer clear, explicit, testable code over clever abstractions.
-   Keep functions focused and modules cohesive.
-   Make state transitions and risk denials observable.
-   Use decimal-safe financial calculations where appropriate.
-   Document all price/point/tick units.
-   Separate simulation/demo behavior from live execution.
-   Fail closed for new entries when risk status is unknown.
-   Do not fail silently.

## 19. Decisions Still Required

These choices must be confirmed before a live-ready implementation:

1.  **Instrument(s):** XAUUSD, forex pairs, crypto CFDs, or other
    symbols?
2.  **Signal source:** existing EA/indicator, manual signal, external
    server, or new strategy?
3.  **Initial direction:** BUY only or BUY and SELL?
4.  **Stage 2/3 entry:** fixed distance only, support confirmation only,
    or hybrid? Recommended: hybrid.
5.  **Exit mode:** individual TP, group TP, or hybrid? Recommended:
    support individual TP first, add group exit after accounting tests.
6.  **SL policy:** individual SL required or optional with mandatory
    aggregate protection? Recommended: SL enabled by default; if
    optional, aggregate protection remains mandatory.
7.  **Risk limits:** maximum account drawdown, aggregate drawdown,
    maximum volume, and minimum margin level. These must be supplied by
    the operator; do not guess.
8.  **Account types/currencies:** exact account denomination and whether
    all accounts use the same broker.
9.  **Repeated signals:** ignore, replace, or add a layer? Recommended:
    ignore duplicates and require explicit layer eligibility.
10. **Position ownership:** should the EA manage only STMA positions or
    also manually opened positions? Recommended: STMA positions only.
11. **Runtime:** local Mac/PC always on, VPS, or other environment?
12. **Notification channel:** none initially or Telegram/email?
13. **Dashboard:** local-only dashboard or remote access?
14. **Emergency close policy:** close all strategy positions, close only
    selected accounts, or pause entries and request operator action?
    Must be explicit and tested.

## 20. Official Technical References

Use official documentation as the source of truth and verify current
behavior before implementation:

-   MQL5 trading functions: https://www.mql5.com/en/docs/trading
-   MQL5 network functions: https://www.mql5.com/en/docs/network
-   MQL5 `OrderCalcProfit`:
    https://www.mql5.com/en/docs/trading/ordercalcprofit
-   MetaTrader 5 account management:
    https://www.metatrader5.com/en/terminal/help/start_advanced/account_manage
-   Exness MetaTrader 5 information:
    https://www.exness.com/metatrader-5/

If documentation conflicts with assumptions in this file, investigate
and update the specification rather than coding around an unverified
assumption.

------------------------------------------------------------------------

## Final Instruction

Build a reliable, testable **trading execution and risk-coordination
system first**. Strategy profitability must be evaluated separately with
realistic backtesting and forward testing. Default to demo, reject
unsafe or ambiguous instructions, preserve auditability, and never
bypass risk controls for the sake of completing an order.
