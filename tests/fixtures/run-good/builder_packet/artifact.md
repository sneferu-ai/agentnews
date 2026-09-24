---
title: Parallel sub-agent research sweeps
summary: Parallel sub-agent sweeps produce higher-quality synthesis than single-agent reading when the evidence base is broad, but coordination overhead dominates on narrow questions.
---

# Parallel sub-agent research sweeps

## Findings

Parallel dispatch of independent sub-agents to gather evidence produced
measurably higher-quality synthesis than a single agent reading all sources
sequentially. The advantage was largest when the evidence base spanned more
than five distinct domains.

**Key result:** cross-domain synthesis quality improved by 34% on broad
questions but degraded by 12% on narrow single-domain questions, where
coordination overhead dominated.

We observed three failure modes worth noting:

- Sub-agents duplicated effort on overlapping sources.
- Synthesis models occasionally dropped a low-salience sub-agent contribution.
- Latency grew super-linearly beyond eight concurrent sub-agents.

### Sub-heading about cost

The cost per synthesis rose roughly linearly with the number of sub-agents,
making the parallel strategy most economical for high-value, broad-scope
questions.

## Methodology

We ran 40 trials across two configurations: (A) a single agent reading all
sources, and (B) eight sub-agents each reading a disjoint source partition.
Synthesis quality was scored by a cross-vendor panel on a 0-100 rubric. All
runs used the same source corpus and the same convergence budget.

## References

[1] **Parallel multi-agent evidence gathering** — https://example.com/paper-1 (Smith, Lee, 2024)
[2] https://example.com/paper-2 — Distributed synthesis under coordination overhead (Jones, 2023)
[3] [Cost-quality tradeoffs in agent teams](https://example.com/paper-3) (Patel, and Kim, 2025)
[4] 4. **Single-agent baselines for research synthesis** — https://example.com/paper-4 (Garcia, 2022)
