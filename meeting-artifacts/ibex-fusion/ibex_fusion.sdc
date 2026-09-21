set clk_period 2.5
create_clock -name vclk -period $clk_period
set_input_delay [expr {0.1 * $clk_period}] -clock vclk [all_inputs]
set_output_delay [expr {0.1 * $clk_period}] -clock vclk [all_outputs]
