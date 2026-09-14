`timescale 1ns/1ps

// Research wrapper around Ibex's real execution block. This is the canonical
// full-ALU + multiplier/divider experiment; the upstream Ibex implementation
// supplies the ALU, MD unit, intermediate registers, and result selection.
module ibex_fused_ex_block_wrapper
  import ibex_pkg::*;
(
  input  logic         clk_i,
  input  logic         rst_ni,
  input  alu_op_e      alu_operator_i,
  input  logic [31:0]  alu_operand_a_i,
  input  logic [31:0]  alu_operand_b_i,
  input  logic         alu_instr_first_cycle_i,
  input  logic [31:0]  bt_a_operand_i,
  input  logic [31:0]  bt_b_operand_i,
  input  md_op_e       multdiv_operator_i,
  input  logic         mult_en_i,
  input  logic         div_en_i,
  input  logic         mult_sel_i,
  input  logic         div_sel_i,
  input  logic [1:0]   multdiv_signed_mode_i,
  input  logic [31:0]  multdiv_operand_a_i,
  input  logic [31:0]  multdiv_operand_b_i,
  input  logic         multdiv_ready_id_i,
  input  logic         data_ind_timing_i,
  output logic [31:0]  result_o,
  output logic [31:0]  alu_adder_result_o,
  output logic         ex_valid_o,
  output logic         branch_decision_o
);
  logic [1:0]   imd_val_we;
  logic [33:0]  imd_val_d[2];
  logic [33:0]  imd_val_q[2];
  logic [31:0]  branch_target_unused;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      imd_val_q <= '{default: '0};
    end else begin
      if (imd_val_we[0]) imd_val_q[0] <= imd_val_d[0];
      if (imd_val_we[1]) imd_val_q[1] <= imd_val_d[1];
    end
  end

  ibex_ex_block #(
    .RV32M(RV32MFast),
    .RV32B(RV32BNone),
    .BranchTargetALU(1'b0)
  ) u_ex_block (
    .clk_i(clk_i), .rst_ni(rst_ni),
    .alu_operator_i(alu_operator_i),
    .alu_operand_a_i(alu_operand_a_i),
    .alu_operand_b_i(alu_operand_b_i),
    .alu_instr_first_cycle_i(alu_instr_first_cycle_i),
    .bt_a_operand_i(bt_a_operand_i), .bt_b_operand_i(bt_b_operand_i),
    .multdiv_operator_i(multdiv_operator_i),
    .mult_en_i(mult_en_i), .div_en_i(div_en_i),
    .mult_sel_i(mult_sel_i), .div_sel_i(div_sel_i),
    .multdiv_signed_mode_i(multdiv_signed_mode_i),
    .multdiv_operand_a_i(multdiv_operand_a_i),
    .multdiv_operand_b_i(multdiv_operand_b_i),
    .multdiv_ready_id_i(multdiv_ready_id_i),
    .data_ind_timing_i(data_ind_timing_i),
    .imd_val_we_o(imd_val_we), .imd_val_d_o(imd_val_d),
    .imd_val_q_i(imd_val_q),
    .alu_adder_result_ex_o(alu_adder_result_o),
    .result_ex_o(result_o),
    .branch_target_o(branch_target_unused),
    .branch_decision_o(branch_decision_o),
    .ex_valid_o(ex_valid_o)
  );
endmodule
