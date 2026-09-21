# Meeting brief — 2026-09-21

## 60–90 second speaking script

“The SUB/LTU result is a useful negative result, but the original comparison was not fair. The matcher found only two common RTLIL nodes: a 32-bit inversion and a 34-bit unsigned add, with coverage of about 29% on SUB and 17% on LTU. The executable graph reconstructed one shared 34-bit adder with selected inputs; it did not share the whole functions, the matched inversion was duplicated in emitted RTL, and LTU’s comparison/result logic remained. Under the equal-interface independent-input contract, separate is 323.456 square microns at 1.33 ns, while fused is 346.598 square microns at 1.46 ns: 23.142 square microns, or 7.155%, worse, despite 43 fewer mapped cells. The mapped explanation is a 56-cell MUX2 population worth 104.272 square microns plus the rest of the changed cell mix; that is evidence of overhead, not a formal causal decomposition. A same-input control saves 2.394 square microns, and the original Ibex ALU already shares that adder, but the independent-input measured policy is REJECT. The next hierarchy result finds a whole Tachyum shifter module, but both binary32 lanes use it simultaneously, so that structural match must also be rejected unless the interface or throughput contract changes.”

## SUB/LTU conclusion

See [the detailed area explanation](sub_ltu_area_explanation.md). The fair result is:

| comparison | area | delay | decision |
|---|---:|---:|---|
| equal-interface separate | 323.456 µm² | 1.33 ns | baseline |
| independent-input fused | 346.598 µm² | 1.46 ns | **REJECT** |
| delta | +23.142 µm² (+7.155%) | +0.130 ns | larger and slower |

The historical 332.500 µm² sum is resource accounting, not this fair baseline. The fused graph shares one 34-bit unsigned adder with selected operands. It does not share the entire SUB/LTU function.

## Hierarchical walkthrough

1. **Module level:** Try a name-independent whole-module match first. In the Tachyum FMA source, `f_mul_add` contains two `right_shifter_74_with_outside_bits` instances for the two binary32 lanes. The focus pair matches the wrapper-plus-child hierarchy 2/2 at 100% coverage. This is a structural whole-module match.
2. **Word-level RTLIL:** If no useful module skeleton exists, flatten to name-independent word-level operators. This is the one intermediate stop used for executable reconstruction. The SUB/LTU case falls through here: it exposes the 34-bit add and 32-bit inversion, and the generated RTL can be simulated and re-extracted.
3. **Gate level:** Use mapped gates for structural observation only. Gate matches are not proof of functional equivalence, not proof of a reconstructable RTL module, and not by themselves a PPA claim.

The current stopping rule is therefore: module → word-level RTLIL → gate. For SUB/LTU, the module-level opportunity was not useful, so the flow descended to RTLIL. For Tachyum, the whole module matched, but source/control behavior rejects fusion: both lanes operate concurrently and feed the same packed result. A one-shifter design would need mutual exclusion, scheduling, or a changed throughput/interface contract.

Evidence links:

- [Hierarchy study report](../2026-09-21/hierarchy_study.md)
- [Tachyum study report](../2026-09-21-tachyum-module-study/report.md)
- [Tachyum study evidence](../2026-09-21-tachyum-module-study/study.json)
- [SUB/LTU matcher result](../2026-09-21/sub_ltu/match.rtlil.json)
- [SUB/LTU executable graph](../2026-09-21/sub_ltu/executable_graph.rtlil.json)
- [Fair interface-control results](../2026-09-21/interface-control/interface_control_results.md)
- [Mapped fair-pair inputs/results](../2026-09-21/interface-control/interface_control_results.json)

## Limitations to state honestly

- The candidate pairs were manually selected; this is not an exhaustive design-wide search.
- A matching module skeleton is structural evidence, not functional equivalence.
- The current hierarchy stop rule can accept a trivial or opaque RTLIL operator match; module-level selection is a candidate signal, not an executable fusion decision.
- Gate/AIG matches are structural only.
- Existing functional checks are directed/deterministic-random evidence, not formal equivalence.
- The fused SUB/LTU interface is selected-source and does not preserve simultaneous independent clients.

## Week 8 action-item status

The 9/14/2026 Timeline items are addressed as follows: SUB/LTU is now forensically explained; a larger Tachyum module-level example is documented; the flow is explicitly module → RTLIL → gate; RTLIL remains the executable reconstruction level; and gate-level matching remains structural. Open work is a broader automatic candidate search and a controlled mux-placement ablation that isolates generator overhead from mapper restructuring.
