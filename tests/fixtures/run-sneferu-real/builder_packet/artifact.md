# Chain-of-thought faithfulness: a measurement protocol

## Findings

Stated reasoning and the computation behind an answer come apart measurably:
perturbing a chain of thought often leaves the answer unchanged, and models
rarely mention the biasing features that moved them.

## Methodology

Three arms (perturbation, early answering, biasing-feature disclosure) scored
per step and aggregated per domain.

## References

- `arXiv:2201.11903` – Wei et al. (2022). Chain-of-Thought Prompting Elicits Reasoning in Large Language Models.
- `arXiv:2305.04388` – Turpin et al. (2023). Language Models Don't Always Say What They Think.
- `arXiv:2307.13702` – Lanham et al. (2023). Measuring Faithfulness in Chain-of-Thought Reasoning.
- `doi:10.48550/arXiv.2305.20050` – Lightman et al. (2023). Let's Verify Step by Step.
- Background reading on mechanistic interpretability (no identifier).
