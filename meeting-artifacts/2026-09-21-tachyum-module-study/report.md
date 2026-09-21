# Tachyum module-level fusion-opportunity study

Date: 2026-09-21. This is a bounded follow-up study; it does not modify historical bundles and does not revisit SUB/LTU or LT/GE except as prior regression context.

## Direct answer

The selected whole module is `right_shifter_74_with_outside_bits`: a 74-bit right shifter with a 26-bit outside/sticky output and 7-bit shift control. The two preserved parent instances are `u_right_shifter_for_addend_single_1` and `u_right_shifter_for_addend_single_2` in `f_mul_add.v`. The name-independent module matcher recognizes the same wrapper-plus-child hierarchy and selects the module level.

This is a structural whole-module match, not an executable or economically useful fusion under the existing FMA contract. The source uses both binary32 lanes concurrently and packs both results into the one 64-bit result every four cycles. A single shared shifter would need a scheduler or a changed interface; it cannot preserve the existing two-lane throughput and latency. PPA is therefore intentionally not run.

## Provenance

- Repository: `https://github.com/Tachyum-Open-Source/fma-rtl.git`
- Exact source revision: `1e7221349e7b18b1e20f4b301f3b7a34b5ebd490`
- Commit: README: point to fp-rtl superset repository (2026-08-21T16:46:56-07:00)
- License: Apache License 2.0 (`third_party/tachyum-fma-rtl/LICENSE`)
- `f_mul_add.v` SHA-256: `4085a123ee242e4ca24863122ef10c053fba0008826d7385b8af8ca1690f5f2c`
- License SHA-256: `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`

## Survey (three pairs maximum)

| pair | preserved instances | result |
|---|---|---|
| `right_shifter_single_lanes` | `u_right_shifter_for_addend_single_1` / `u_right_shifter_for_addend_single_2` | selected; structurally recognized, rejected for throughput-preserving fusion |
| `left_shifter_single_lanes` | `left_shifter_76_1` / `left_shifter_76_2` | surveyed; not selected |
| `leading_zero_single_lanes` | `u_leading_zeros_detector_for_zeros_single_1` / `u_leading_zeros_detector_for_zeros_single_2` | surveyed; not selected |

## Top-down graph walk for the selected pair

The focus wrappers preserve the actual child module and deliberately use different top/instance names. The matcher compares semantic module signatures and hierarchy edges, not those names.

The actual parent-level extraction is persisted separately: `graphs/parent_f_mul_add.module.graph.json` contains 139 module nodes, and its `f_mul_add` node has an edge to `right_shifter_74_with_outside_bits` with instance count 2. The focus wrappers isolate those two real repeated instances for a pairwise comparison.

| level | matched nodes | matched operators | largest connected match | coverage A/B | runtime (s) | graph sizes A; B |
|---|---:|---:|---:|---|---:|---|
| module | 2 | 2 | 2 | 1.000/1.000 | 0.000142 | 2/1; 2/1 |
| rtlil | 17 | 7 | 6 | 0.895/0.895 | 0.063529 | 19/31; 19/31 |
| gate | 332 | 328 | 6 | 0.432/0.432 | 0.150584 | 768/1984; 768/1984 |

- **Module:** the wrapper top and the `right_shifter_74_with_outside_bits` child form a preserved hierarchy skeleton, so the policy stops here for candidate selection.
- **Word-level RTLIL:** the same pair was still extracted and matched as a cross-check; this is the reconstructable operator view, not the selected fusion level for this pair.
- **Gate:** generic mapped-gate similarity is reported as structural evidence only. It is not used to claim that a shared RTL module can be reconstructed or that PPA improves.

Figures and JSON: `figures/right_shifter_single_lanes/`, `graphs/right_shifter_single_lanes/`, and `matches/right_shifter_single_lanes.json`.

## Contract and blocker

| field | frozen value |
|---|---|
| interface | existing f_mul_add packed 64-bit binary32 mode; two independent 32-bit lane results and four per-lane flags |
| latency | 4 cycles |
| throughput | one packed operation per cycle (source README contract; both binary32 lanes participate in one operation) |
| mutual exclusion | none: the two selected shifters are parallel lane resources and both may be active for the same format_single transaction |
| sharing contract required | at most one lane request may use the shared 74-bit shifter at a time, with a scheduler or a changed packed-lane interface |

The source control behavior rules out the required mutual exclusion: `format_single` enables both single lanes, each shifter has independent data and shift signals, and both lane results are concatenated into `result[63:0]`. The source README fixes four-cycle latency and data-valid gating; the source also describes the packed two-lane binary32 mode. The exact source-line evidence is in `study.json` under `source_context`.

## What remains unsolved

A useful optimization would require a new scheduling contract: one lane at a time, explicit lane selection, and either increased latency or a different packed-result protocol. That is a larger redesign than this bounded search. No fused RTL, equal-interface baseline, simulation claim, or PPA number is reported because constructing one would violate the frozen source contract.

## Reproduction

```sh
python3 scripts/tachyum_module_study.py
python3 -m unittest tests.test_tachyum_module_study -v
```
