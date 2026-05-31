# `core/` — Generic framework (Layer 1)

The domain-agnostic engine. This is the reusable IP. **Nothing here knows what
a bet, a flight, or a crypto candle is.** Domain knowledge lives in `adapters/`.

The core consumes adapters through the `Adapter` interface (`adapters/base.py`)
and orchestrates the full pipeline:

```
ingestion → models → signals → resolution → output
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `ingestion/` | Pulls data from configured sources (batch, polling, or streaming) and writes immutable `raw_events`. Handles dedup via `event_key`, retries, and source-down alerts. |
| `models/` | Runs the model an adapter provides over `raw_events`. Records every execution as a `model_run` for versioning and comparison. |
| `signals/` | Turns model output into `signals`: computes expected value, confidence score, and ranking. Domain-neutral math. |
| `resolution/` | Resolves outcomes once the real event happens, applying the signal's `resolution_rule` (`binary` / `threshold` / `continuous`). Computes metrics like CLV. |
| `output/` | Delivers signals through channels: email, Telegram, webhook. Supports daily batch and real-time per-event dispatch. |

## Design rule

The core never imports from a specific adapter. The dependency points one way:
adapters depend on the core's interfaces, never the reverse. If you find
yourself adding `if domain == "betting"` here, it belongs in an adapter instead.
