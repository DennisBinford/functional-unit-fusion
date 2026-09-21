// Same-interface duplicated control: separate SUB and LTU cones.
// operation_select_i=1 selects SUB; operation_select_i=0 selects LTU.
module same_input_separate(
    input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
    input logic operation_select_i, output logic [31:0] result_o);
  logic [31:0] sub_result, ltu_result;
  ibex_alu_sub32 sub_cone(.operand_a_i(operand_a_i), .operand_b_i(operand_b_i), .result_o(sub_result));
  ibex_alu_ltu32 ltu_cone(.operand_a_i(operand_a_i), .operand_b_i(operand_b_i), .result_o(ltu_result));
  assign result_o = operation_select_i ? sub_result : ltu_result;
endmodule
