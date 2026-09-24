# AgentNews — Build-Ready Product Contract (B13) — Brand Identity

This file is a launch-frozen product contract. Read `BRAND_DIRECTION.md` first. Use the exact selected brand-mark bytes; do not redraw, replace, recolor, or generate another logo during the UI pass.

- Identity ID: `4b55c15858f98a6a6414d6e8f0e147b790b928567e32c09c8bdfefde9a9df31a`
- Direction asset: `BRAND_ASSETS/brand-direction.svg`
- Selected mark: `BRAND_ASSETS/brand-mark.svg`
- Mark source: `deterministic_fallback`
- Board-to-mark lineage: `fallback/debt`

## Required semantic color tokens

- `--brand-background`: `#0D1418`
- `--brand-surface`: `#162329`
- `--brand-foreground`: `#F4F0E6`
- `--brand-muted`: `#A9B5B7`
- `--brand-primary`: `#F2A65A`
- `--brand-secondary`: `#55C1A7`

Copy the exact selected mark into the framework's public/static asset directory and reference that copy in the primary visible product surface. Apply every semantic color through the product's token layer. The direction board remains a design reference and must not be mistaken for the shipped mark. The orchestrator verifies the copied mark bytes, source reference, complete palette, and direction acknowledgement before B16 may pass.
