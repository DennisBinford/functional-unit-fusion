`timescale 1ns/1ps

// Independent client used by the Priority 2 comparison.  It has no fusion
// control; the mode selector belongs only to the enclosing comparison wrapper.
module ibex_independent_add_client (
  input  logic [31:0] operand_a_i,
  input  logic [31:0] operand_b_i,
  output logic [31:0] result_o
);
  assign result_o = operand_a_i + operand_b_i;
endmodule

// Equal-interface separate baseline: canonical whole Ibex ALU plus a private
// dedicated adder.  client_select_i=0 selects the ALU, 1 selects the client.
module ibex_alu_add_client_separate
  import ibex_pkg::*;
(
  input  logic [6:0]  operator_i,
  input  logic [31:0] alu_operand_a_i,
  input  logic [31:0] alu_operand_b_i,
  input  logic [31:0] client_operand_a_i,
  input  logic [31:0] client_operand_b_i,
  input  logic        client_select_i,
  output logic [31:0] result_o
);
  alu_op_e operator;
  logic [31:0] alu_result;
  logic cmp_result;
  logic equal_result_unused;
  logic [31:0] imd_val_d_unused [2];
  logic [1:0] imd_val_we_unused;
  logic [31:0] adder_result_unused;
  logic [33:0] adder_result_ext_unused;
  logic [31:0] client_result;

  assign operator = alu_op_e'(operator_i);
  ibex_alu #(.RV32B(RV32BNone)) u_ibex_alu (
    .operator_i(operator), .operand_a_i(alu_operand_a_i),
    .operand_b_i(alu_operand_b_i), .instr_first_cycle_i(1'b1),
    .multdiv_operand_a_i(33'b0), .multdiv_operand_b_i(33'b0),
    .multdiv_sel_i(1'b0), .imd_val_q_i('{32'b0, 32'b0}),
    .imd_val_d_o(imd_val_d_unused), .imd_val_we_o(imd_val_we_unused),
    .adder_result_o(adder_result_unused),
    .adder_result_ext_o(adder_result_ext_unused), .result_o(alu_result),
    .comparison_result_o(cmp_result), .is_equal_result_o(equal_result_unused)
  );
  ibex_independent_add_client u_client (
    .operand_a_i(client_operand_a_i), .operand_b_i(client_operand_b_i),
    .result_o(client_result)
  );

  assign result_o = client_select_i ? client_result :
                    ((operator inside {ALU_LT, ALU_LTU, ALU_GE, ALU_GEU, ALU_EQ, ALU_NE})
                     ? {31'b0, cmp_result} : alu_result);
endmodule
