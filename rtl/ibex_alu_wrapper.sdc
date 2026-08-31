# Timing constraints for the isolated Ibex ALU functional unit. Like demo_alu it
# is combinational (no clock port), so the input->output path is constrained
# against a virtual clock.
#
# 2.5 ns target (FreePDK45 / Nangate45). This is the closure point found by
# `timing_closure.py --design ibex_alu_wrapper`: the unit's own critical path is
# 1.95 ns, plus 10% of the period reserved at each boundary.
#
# History / why this matters: this file previously carried a 50 ns period left
# over from the sky130 flow. At 50 ns the fixed 10 ns I/O reservation dominated
# the 1.95 ns of logic, so OpenSTA's fmax reported 83.7 MHz for a unit that
# actually runs at 513 MHz -- a 6.1x understatement. Worse, demo_alu reported
# 88.7 MHz under the same constraint, making the two units look 6% apart when
# their real critical paths differ by 52%. Keep the period near closure.
set clk_period 2.5

create_clock -name vclk -period $clk_period

set_input_delay  [expr {0.1 * $clk_period}] -clock vclk [all_inputs]
set_output_delay [expr {0.1 * $clk_period}] -clock vclk [all_outputs]
