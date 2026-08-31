# Timing constraints for the combinational demo_alu functional unit.
# demo_alu has no clock port, so we constrain the input->output combinational
# path against a virtual clock. Period is the target cycle time budget the
# functional unit must fit within; input/output delays reserve part of that
# budget for surrounding logic so the reported slack reflects the unit alone.
#
# 1.6 ns target (FreePDK45 / Nangate45). This is the closure point found by
# `timing_closure.py --design demo_alu`: the unit's own critical path is
# 1.28 ns, plus 10% of the period reserved at each boundary.
#
# History / why this matters: this file previously carried a 50 ns period left
# over from the sky130 flow. At 50 ns the I/O reservation alone was 10 ns while
# the logic was 1.28 ns, so OpenSTA's fmax (= 1/min_period for the whole
# constraint) reported 88.7 MHz for a unit that actually runs at 781 MHz -- an
# 8.8x understatement, and both ALUs in the study collapsed to ~85 MHz
# regardless of their real speed. Keep the period near closure so the
# constraint stays binding and the reported numbers mean something.
set clk_period 1.6

create_clock -name vclk -period $clk_period

set_input_delay  [expr {0.1 * $clk_period}] -clock vclk [all_inputs]
set_output_delay [expr {0.1 * $clk_period}] -clock vclk [all_outputs]
