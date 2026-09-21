module ibex_alu_add_direct(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i, output logic [31:0] result_o);
  import ibex_pkg::*;
  logic [31:0] raw;
  ibex_alu #(.RV32B(RV32BNone)) u(.operator_i(ALU_ADD), .operand_a_i(operand_a_i), .operand_b_i(operand_b_i), .instr_first_cycle_i(1'b1), .multdiv_operand_a_i(33'b0), .multdiv_operand_b_i(33'b0), .multdiv_sel_i(1'b0), .imd_val_q_i(), .imd_val_d_o(), .imd_val_we_o(), .adder_result_o(), .adder_result_ext_o(), .result_o(raw), .comparison_result_o(), .is_equal_result_o());
  assign result_o = raw;
endmodule
