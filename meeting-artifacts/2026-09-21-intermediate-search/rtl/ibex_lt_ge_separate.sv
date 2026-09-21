// Equal-interface baseline for the LT/GE selected-source comparison.
module ibex_lt_ge_separate (
    input logic [31:0] b_operand_a_i,
    input logic [31:0] b_operand_b_i,
    input logic [31:0] operand_a_i,
    input logic [31:0] operand_b_i,
    input logic source_select_i,
    output logic [31:0] result_o
);
    logic [31:0] lt_result;
    logic [31:0] ge_result;
    ibex_alu_lt32 u_lt(.operand_a_i(operand_a_i), .operand_b_i(operand_b_i), .result_o(lt_result));
    ibex_alu_ge32 u_ge(.operand_a_i(b_operand_a_i), .operand_b_i(b_operand_b_i), .result_o(ge_result));
    assign result_o = source_select_i ? lt_result : ge_result;
endmodule
