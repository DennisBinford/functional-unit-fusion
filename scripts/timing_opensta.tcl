# OpenSTA timing and activity-based power analysis for a combinational unit.
read_liberty $env(FU_LIBERTY)
read_verilog $env(FU_NETLIST)
link_design -no_black_boxes $env(FU_TOP)

set clock_period $env(FU_CLOCK_PERIOD)
create_clock -name virtual_clock -period $clock_period
set io_delay [expr {$clock_period * 0.10}]
set_input_delay $io_delay -clock virtual_clock [all_inputs]
set_output_delay $io_delay -clock virtual_clock [all_outputs]
set_input_transition $env(FU_INPUT_TRANSITION) [all_inputs]
set_load $env(FU_OUTPUT_LOAD) [all_outputs]

check_setup -verbose > "$env(FU_BUILD_DIR)/check_setup.rpt"
report_units > "$env(FU_BUILD_DIR)/units.rpt"
report_checks -path_delay max -group_path_count 10 -digits 6 > "$env(FU_BUILD_DIR)/timing.rpt"
report_worst_slack -max -digits 6 > "$env(FU_BUILD_DIR)/slack.rpt"

# The VCD comes from the functional regression. This is useful for relative
# exploration but is not a representative-product workload by itself.
read_vcd -scope $env(FU_ACTIVITY_SCOPE) $env(FU_ACTIVITY_VCD)
report_activity_annotation -report_annotated -report_unannotated \
  > "$env(FU_BUILD_DIR)/activity_annotation.rpt"
report_power -digits 8 > "$env(FU_BUILD_DIR)/power.rpt"
