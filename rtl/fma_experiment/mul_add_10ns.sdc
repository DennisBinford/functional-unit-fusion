# Common 100 MHz constraint for the registered ADD/MUL comparison.
set clk_period 10.0
create_clock -name design_clock -period $clk_period [get_ports clk_i]
set data_inputs [get_ports {rst_ni valid_i add_i a_i b_i}]
set_input_delay  1.0 -clock design_clock $data_inputs
set_output_delay 1.0 -clock design_clock [all_outputs]
