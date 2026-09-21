`timescale 1ns/1ps

// Constant-operation specializations of the pinned upstream Ibex ALU.
// These wrappers contain no replacement arithmetic: every operation is driven
// by third_party/ibex/rtl/ibex_alu.sv. The comparison wrappers only adapt the
// upstream one-bit comparison result to the common 32-bit FU result port.
module ibex_alu_operation_wrapper #(
  parameter ibex_pkg::alu_op_e OP = ibex_pkg::ALU_ADD,
  parameter bit IS_COMPARISON = 1'b0
) (
  input logic [31:0] operand_a_i,
  input logic [31:0] operand_b_i,
  output logic [31:0] result_o
);
  import ibex_pkg::*;
  logic [31:0] alu_result;
  logic comparison_result;

  ibex_alu #(.RV32B(RV32BNone)) u_ibex_alu (
    .operator_i(OP), .operand_a_i(operand_a_i), .operand_b_i(operand_b_i),
    .instr_first_cycle_i(1'b1), .multdiv_operand_a_i(33'b0),
    .multdiv_operand_b_i(33'b0), .multdiv_sel_i(1'b0),
    .imd_val_q_i(), .imd_val_d_o(), .imd_val_we_o(),
    .adder_result_o(), .adder_result_ext_o(), .result_o(alu_result),
    .comparison_result_o(comparison_result), .is_equal_result_o()
  );

  assign result_o = IS_COMPARISON ? {31'b0, comparison_result} : alu_result;
endmodule

module ibex_alu_add32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                      output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_ADD)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_sub32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                      output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_SUB)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_lt32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                     output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_LT), .IS_COMPARISON(1'b1)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_ltu32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                      output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_LTU), .IS_COMPARISON(1'b1)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_ge32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                     output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_GE), .IS_COMPARISON(1'b1)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_geu32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                      output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_GEU), .IS_COMPARISON(1'b1)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_eq32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                     output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_EQ), .IS_COMPARISON(1'b1)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_ne32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                     output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_NE), .IS_COMPARISON(1'b1)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_xor32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                      output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_XOR)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_or32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                     output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_OR)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_and32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                      output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_AND)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_sll32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                      output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_SLL)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_srl32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                      output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_SRL)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
module ibex_alu_sra32(input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
                      output logic [31:0] result_o);
  ibex_alu_operation_wrapper #(.OP(ibex_pkg::ALU_SRA)) u(.operand_a_i, .operand_b_i, .result_o);
endmodule
