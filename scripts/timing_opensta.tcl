# OpenSTA timing and activity-based power analysis for a combinational unit.
read_liberty $env(FU_LIBERTY)
read_verilog $env(FU_NETLIST)
link_design -no_black_boxes $env(FU_TOP)

set clock_period $env(FU_CLOCK_PERIOD)
if {[info exists env(FU_CLOCK_PORT)] && $env(FU_CLOCK_PORT) ne ""} {
  set clock_pin [get_ports $env(FU_CLOCK_PORT)]
  create_clock -name design_clock -period $clock_period $clock_pin
  # Do not apply a data-input delay to the port that owns the clock.  OpenSTA
  # 3.x diagnoses that combination and it can obscure the actual data path.
  set constrained_inputs [get_ports -filter "direction == input && name != $env(FU_CLOCK_PORT)" *]
  set constraint_clock design_clock
} else {
  create_clock -name virtual_clock -period $clock_period
  set constrained_inputs [all_inputs]
  set constraint_clock virtual_clock
}
set io_delay [expr {$clock_period * 0.10}]
set_input_delay $io_delay -clock $constraint_clock $constrained_inputs
set_output_delay $io_delay -clock $constraint_clock [all_outputs]
set_input_transition $env(FU_INPUT_TRANSITION) $constrained_inputs
set_load $env(FU_OUTPUT_LOAD) [all_outputs]

check_setup -verbose > "$env(FU_BUILD_DIR)/check_setup.rpt"
# report_units is a Tcl procedure without shell-redirection support in the
# current OpenSTA. Capture its output with the supported helper instead.
set units_file [open "$env(FU_BUILD_DIR)/units.rpt" w]
puts $units_file [with_output_to_variable units_text {report_units}]
close $units_file
report_checks -path_delay max -group_path_count 10 -digits 6 > "$env(FU_BUILD_DIR)/timing.rpt"
report_worst_slack -max -digits 6 > "$env(FU_BUILD_DIR)/slack.rpt"

# The VCD comes from the functional regression. This is useful for relative
# exploration but is not a representative-product workload by itself.
read_vcd -scope $env(FU_ACTIVITY_SCOPE) $env(FU_ACTIVITY_VCD)
report_activity_annotation -report_annotated -report_unannotated \
  > "$env(FU_BUILD_DIR)/activity_annotation.rpt"
report_power -digits 8 > "$env(FU_BUILD_DIR)/power.rpt"
