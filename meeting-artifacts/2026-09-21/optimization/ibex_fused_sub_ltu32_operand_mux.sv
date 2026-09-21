module ibex_fused_sub_ltu32_operand_mux(
    input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
    input logic [31:0] b_operand_a_i, input logic [31:0] b_operand_b_i,
    input logic source_select_i, output logic [31:0] result_o);
  logic [31:0] selected_a, selected_b, selected_b_not;
  logic [33:0] shared_operator;
  logic [31:0] sub_result;
  logic ltu_result;
  assign selected_a = source_select_i ? operand_a_i : b_operand_a_i;
  assign selected_b = source_select_i ? operand_b_i : b_operand_b_i;
  assign selected_b_not = ~selected_b;
  assign shared_operator = {1'b0, selected_a, 1'b1} +
                           {1'b0, selected_b_not, 1'b1};
  assign sub_result = shared_operator[32:1];
  assign ltu_result = (selected_a[31] ^ selected_b[31]) ?
                      ~selected_a[31] : shared_operator[32];
  assign result_o = source_select_i ? sub_result : {31'b0, ltu_result};
endmodule
