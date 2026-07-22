# Timing constraints for the isolated Ibex ALU functional unit. Like demo_alu it
# is combinational (no clock port), so the input->output path is constrained
# against a virtual clock. 50 ns is a comfortable target for the base RV32I
# datapath in the slow sky130 process; tighten it to explore the timing wall.

set clk_period 50.0

create_clock -name vclk -period $clk_period

set_input_delay  [expr {0.1 * $clk_period}] -clock vclk [all_inputs]
set_output_delay [expr {0.1 * $clk_period}] -clock vclk [all_outputs]
