`timescale 1ns/1ps

// Isolates lowRISC Ibex's `ibex_alu` (third_party/ibex/rtl/ibex_alu.sv) as a
// standalone combinational functional unit for the evaluation framework.
//
// Configuration is fixed to RV32BNone, so only the base RV32I operations are
// active: ADD, SUB, XOR, OR, AND, SLL, SRL, SRA, and the comparisons
// (LT, LTU, GE, GEU, EQ, NE). The multi-cycle multiply/divide and the
// bit-manipulation intermediate-value ports are not used by these operations
// and are tied to documented constants, exactly as the Ibex ALU documentation
// describes for single-cycle base operations.
//
// The Ibex ALU splits its outputs: arithmetic/logic/shift results appear on
// `result_o`, while comparisons drive the 1-bit `comparison_result_o`. To
// present a single clean functional-unit result, this wrapper zero-extends the
// comparison bit into a 32-bit result for the comparison operators and passes
// `result_o` through for everything else.
module ibex_alu_wrapper
  import ibex_pkg::*;
(
  input  logic [6:0]  operator_i,   // encodes ibex_pkg::alu_op_e
  input  logic [31:0] operand_a_i,
  input  logic [31:0] operand_b_i,
  output logic [31:0] result_o
);

  alu_op_e             operator;
  logic        [31:0]  alu_result;
  logic                cmp_result;
  logic                is_equal_unused;
  logic        [31:0]  imd_d_unused   [2];
  logic        [1:0]   imd_we_unused;
  logic        [31:0]  adder_result_unused;
  logic        [33:0]  adder_result_ext_unused;

  assign operator = alu_op_e'(operator_i);

  ibex_alu #(
    .RV32B (RV32BNone)
  ) u_ibex_alu (
    .operator_i          (operator),
    .operand_a_i         (operand_a_i),
    .operand_b_i         (operand_b_i),

    // Single-cycle base ops: assert first cycle, no multiply/divide operand path.
    .instr_first_cycle_i (1'b1),
    .multdiv_operand_a_i (33'b0),
    .multdiv_operand_b_i (33'b0),
    .multdiv_sel_i       (1'b0),

    // Intermediate values are only used by multi-cycle RV32B ops (disabled here).
    .imd_val_q_i         ('{32'b0, 32'b0}),
    .imd_val_d_o         (imd_d_unused),
    .imd_val_we_o        (imd_we_unused),

    // Raw adder taps are not part of the functional-unit result.
    .adder_result_o      (adder_result_unused),
    .adder_result_ext_o  (adder_result_ext_unused),

    .result_o            (alu_result),
    .comparison_result_o (cmp_result),
    .is_equal_result_o   (is_equal_unused)
  );

  logic is_comparison;
  always_comb begin
    unique case (operator)
      ALU_LT, ALU_LTU, ALU_GE, ALU_GEU, ALU_EQ, ALU_NE: is_comparison = 1'b1;
      default:                                           is_comparison = 1'b0;
    endcase
  end

  assign result_o = is_comparison ? {31'b0, cmp_result} : alu_result;

endmodule
