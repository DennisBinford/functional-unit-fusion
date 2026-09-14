set clk_period 10.0
create_clock -name virtual_clock -period $clk_period
set_input_delay 1.0 -clock virtual_clock [all_inputs]
set_output_delay 1.0 -clock virtual_clock [all_outputs]
