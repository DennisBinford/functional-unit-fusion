# SUB/LTU area explanation

## Bottom line

The fusion did not merge “SUB” and “LTU” as complete functions. The exact RTLIL matcher found only two common nodes:

1. one 32-bit operand inversion (`$not`), and
2. one 34-bit unsigned `$add`.

The matched-node coverage was limited: 2/7 nodes on the SUB side (0.285714) and 2/12 on the LTU side (0.166667). The matcher therefore identified a small common cone, not a shared subtract unit or a shared less-than function. Evidence: [RTLIL match](../2026-09-21/sub_ltu/match.rtlil.json), [forensics](../2026-09-21/sub_ltu_forensics.json).

## What the executable result actually shares

The generated design has one shared 34-bit unsigned operator, `w_shared_operator`. Its two inputs are selected through `source_select_i` and then computed once:

```text
separate baseline                         generated fused result
------------------                        ----------------------
SUB operands -> 34-bit add -> SUB result  SUB operands --+       +-> SUB slice/result
                                                        |-> mux -> 34-bit add
LTU operands -> 34-bit add -> LTU compare -> LTU result +       +-> LTU compare/result
```

The actual generated RTL shows two 34-bit input muxes feeding `w_shared_operator`, one shared addition, and one result-selection mux. It also retains LTU-specific XOR/equality/inversion/result logic. The matched inversion is **not** coalesced in the emitted RTL: the generated file contains separate `~operand_b_i` and `~b_operand_b_i` assignments. Evidence: [generated RTL](../2026-09-21/sub_ltu/generated_rtl.sv), [executable graph/control specification](../2026-09-21/sub_ltu/executable_graph.rtlil.json).

This is selected-source, independent-input, time-multiplexed sharing: two operand pairs enter the module, but only one result is externally visible at a time. It does not preserve simultaneous SUB and LTU client use.

## Fair measurement

The historical 332.500 µm² value is the literal sum of separately measured cores. It is useful resource accounting, but it is not the fair baseline because it does not include the equal external interface with two independent operand pairs and one selected result.

The fair comparison uses the same interface, same constraints, same Liberty/Yosys/OpenSTA flow, and the same 262-vector / 524-check functional workload:

| design | area | critical delay | mapped cells | pre-tech `$add` | pre-tech `$mux` | simulation |
|---|---:|---:|---:|---:|---:|---|
| independent equal-interface separate | 323.456 µm² | 1.33 ns | 346 | 2 | 5 | 524/524 pass |
| independent-input fused | 346.598 µm² | 1.46 ns | 303 | 1 | 4 | 524/524 pass |
| fused − separate | **+23.142 µm² (+7.155%)** | **+0.130 ns** | −43 | −1 | −1 | — |

Evidence: [fair control results](../2026-09-21/interface-control/interface_control_results.md), [machine-readable results](../2026-09-21/interface-control/interface_control_results.json), and the two simulation logs in that directory.

## Fair mapped cell-count and Liberty-area reconciliation

The following table is recomputed from the persisted fair-pair cell populations in `interface_control_results.json`. Each area delta is `(fused count − separate count) × Nangate45 typical Liberty cell area`. The Liberty-area sum reconciles to the measured **+23.142 µm²**; cell counts reconcile from 346 to 303.

| mapped cell | separate | fused | count Δ | area each | area Δ |
|---|---:|---:|---:|---:|---:|
| AND2_X1 | 1 | 5 | +4 | 1.064 | +4.256 |
| AND3_X1 | 7 | 5 | −2 | 1.330 | −2.660 |
| AOI211_X1 | 3 | 2 | −1 | 1.330 | −1.330 |
| AOI21_X1 | 22 | 45 | +23 | 1.064 | +24.472 |
| AOI221_X1 | 5 | 0 | −5 | 1.596 | −7.980 |
| AOI22_X1 | 7 | 0 | −7 | 1.330 | −9.310 |
| BUF_X1 | 2 | 11 | +9 | 0.798 | +7.182 |
| INV_X1 | 96 | 54 | −42 | 0.532 | −22.344 |
| MUX2_X1 | 0 | 56 | +56 | 1.862 | **+104.272** |
| NAND2_X1 | 33 | 10 | −23 | 0.798 | −18.354 |
| NAND3_X1 | 11 | 0 | −11 | 1.064 | −11.704 |
| NOR2_X1 | 60 | 34 | −26 | 0.798 | −20.748 |
| NOR3_X1 | 22 | 4 | −18 | 1.064 | −19.152 |
| NOR4_X1 | 1 | 0 | −1 | 1.330 | −1.330 |
| OAI211_X2 | 0 | 2 | +2 | 2.394 | +4.788 |
| OAI21_X1 | 23 | 39 | +16 | 1.064 | +17.024 |
| OAI221_X1 | 5 | 0 | −5 | 1.596 | −7.980 |
| OAI22_X1 | 11 | 1 | −10 | 1.330 | −13.300 |
| OAI33_X1 | 0 | 1 | +1 | 1.862 | +1.862 |
| OR2_X1 | 2 | 1 | −1 | 1.064 | −1.064 |
| OR3_X1 | 0 | 1 | +1 | 1.330 | +1.330 |
| OR4_X1 | 0 | 1 | +1 | 1.596 | +1.596 |
| XNOR2_X1 | 4 | 3 | −1 | 1.596 | −1.596 |
| XOR2_X1 | 31 | 28 | −3 | 1.596 | −4.788 |
| **total** | **346** | **303** | **−43** | — | **+23.142** |

The fused implementation has fewer total cells but more mapped area because synthesis changes the cell mix. In particular, the fused netlist has 56 `MUX2_X1` cells where the fair separate baseline has none; at 1.862 µm² each, that population contributes 104.272 µm². This is a large observed population, not an isolated causal cost: other cell populations change at the same time, including reductions in XNOR, INV, NAND, NOR, and other cells and increases in AOI/OAI/XOR families. The table accounts for the measured delta but does not prove a gate-by-gate causal decomposition.

The pre-technology count of 5 `$mux` nodes versus 4 is misleading if read without widths and mapped context. A node count does not say how many bits each mux selects or how technology mapping restructures the surrounding cone. The fused design has two 34-bit operand-selection muxes plus a 32-bit output selection in the generated RTL; the separate wrapper’s five pre-tech mux nodes belong to two separate operation cones and their own control structure. Mapping can turn these structures into a different population of standard cells, so fewer RTL mux nodes and fewer total mapped cells do not imply less area.

## What was tried after the regression

- Moving selection before operand preparation/inversion produced a candidate at **345.002 µm² / 1.44 ns**, versus 346.598 µm² / 1.46 ns for the original fused candidate. It passed the same 524 checks, but it still loses to the fair separate baseline at 323.456 µm² / 1.33 ns. This is a candidate improvement, not a successful fusion decision. Evidence: [optimization result](../2026-09-21/optimization/result.json).
- The same-input control, `same_input_separate` versus `same_input_shared`, saved **2.394 µm²** (226.100 → 223.706 µm²) in a different within-ALU contract. The original Ibex ALU already routes SUB/LTU through one adder result, so this is corroborating evidence, not a newly discovered independent-client fusion. Evidence: [same-input evidence](../2026-09-21/interface-control/original_ibex_same_input_evidence.json) and [control table](../2026-09-21/interface-control/interface_control_results.md).
- The independent-input measured policy decision is **REJECT**: the equal-interface fused design is larger and slower than the equal-interface separate baseline.

## Why the expected savings did not appear

The expectation assumed that removing one adder would dominate the cost. The evidence shows a much smaller structural match than “the SUB and LTU units are the same”: only one 34-bit adder and one 32-bit inversion matched, with LTU comparison/result logic retained. The executable construction then adds selection around the shared operator, and the mapper realizes that control as a large `MUX2_X1` population plus a changed mix of compound gates. The 104.272 µm² MUX2 population is the clearest observed explanation for the direction of the result, while the full +23.142 µm² delta is the reconciled sum of all cell-population changes.

The remaining causal uncertainty is how much of the mux population is intrinsically required by the chosen independent-input interface versus how much is a consequence of this particular generated RTL placement and the mapper’s Boolean restructuring. The smallest useful future ablation is a controlled three-way comparison under the same interface and constraints: (1) separate baseline, (2) current fused RTL, and (3) a hand-written fused RTL that shares only the 34-bit adder while holding operand preparation, LTU result logic, and output selection structurally fixed. Compare pre-tech widths and mapped cell deltas. Do not infer causality from the current 5-versus-4 `$mux` node count alone.
