// Same-interface within-ALU control: one shared 34-bit subtract path.
// operation_select_i=1 selects SUB; operation_select_i=0 selects LTU.
module same_input_shared(
    input logic [31:0] operand_a_i, input logic [31:0] operand_b_i,
    input logic operation_select_i, output logic [31:0] result_o);
  logic [33:0] shared_operator;
  logic [31:0] sub_result;
  logic ltu_result;
  assign shared_operator = {1'b0, operand_a_i, 1'b1} +
                           {1'b0, ~operand_b_i, 1'b1};
  assign sub_result = shared_operator[32:1];
  assign ltu_result = (operand_a_i[31] ^ operand_b_i[31]) ?
                      ~operand_a_i[31] : shared_operator[32];
  assign result_o = operation_select_i ? sub_result : {31'b0, ltu_result};
endmodule
