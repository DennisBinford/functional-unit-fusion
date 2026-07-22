# Timing constraints for the combinational demo_alu functional unit.
# demo_alu has no clock port, so we constrain the input->output combinational
# path against a virtual clock. Period is the target cycle time budget the
# functional unit must fit within; input/output delays reserve part of that
# budget for surrounding logic so the reported slack reflects the unit alone.

# 50 ns target: comfortably met by the unpipelined combinational datapath in the
# slow sky130 process (critical path ~32 ns), so the demo reports positive slack
# alongside the achievable fmax. Tighten this to explore the timing wall.
set clk_period 50.0

create_clock -name vclk -period $clk_period

set_input_delay  [expr {0.1 * $clk_period}] -clock vclk [all_inputs]
set_output_delay [expr {0.1 * $clk_period}] -clock vclk [all_outputs]
