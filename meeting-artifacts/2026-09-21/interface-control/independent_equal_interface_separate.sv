// Equal-interface independent-input control: two separate operation cones.
module independent_equal_interface_separate(
    input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
    input logic [31:0] b_operand_a_i, input logic [31:0] b_operand_b_i,
    input logic source_select_i, output logic [31:0] result_o);
  logic [31:0] sub_result, ltu_result;
  ibex_alu_sub32 sub_client(.operand_a_i(operand_a_i), .operand_b_i(operand_b_i), .result_o(sub_result));
  ibex_alu_ltu32 ltu_client(.operand_a_i(b_operand_a_i), .operand_b_i(b_operand_b_i), .result_o(ltu_result));
  assign result_o = source_select_i ? sub_result : ltu_result;
endmodule
