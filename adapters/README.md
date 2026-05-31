# `adapters/` — Domain adapters (Layer 2)

One plug-in per domain or client. Each adapter teaches the generic core how to
operate in a specific business. **Adding a new domain means adding a folder
here — the `core/` is never modified.**

## The contract

Every adapter implements the `Adapter` interface defined in `base.py`. At a
minimum it answers four questions for the core:

1. **How to ingest** — which source, what cadence, how to extract an
   `event_key` from a payload.
2. **How to model** — the model that runs over `raw_events` and what features it
   produces.
3. **How to score** — how raw model output maps to confidence and expected value.
4. **How to resolve** — the `resolution_rule` and how to compute the actual
   outcome from real-world results.

## Current adapters

| Adapter | Data source | Model | Resolution | Cadence |
|---------|-------------|-------|------------|---------|
| `betting/` | Odds APIs + stats | Poisson | binary | Daily batch |
| `flights/` | Amadeus | Time series | threshold | Polling |
| `realestate/` | Comparables | Regression | continuous | Batch |
| `crypto/` | Binance | Technical analysis | threshold | Streaming |

The fact that these four signatures are completely different — yet all run on
the same unchanged core — is the proof of the framework's genericity.

## Adding an adapter

```
adapters/
└── your_domain/
    ├── __init__.py
    ├── ingestion.py   # source config + payload → event_key
    ├── model.py       # the model + feature engineering
    └── resolution.py  # resolution_rule + outcome computation
```

Implement the interface, register the adapter, configure its data source. Done.
No core changes.
