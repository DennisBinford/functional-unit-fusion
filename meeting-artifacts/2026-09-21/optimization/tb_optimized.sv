`timescale 1ns/1ps
module tb_ibex_fusion;
  logic [31:0] operand_a_i, operand_b_i, b_operand_a_i, b_operand_b_i;
  logic source_select_i;
  logic [31:0] result_o, result_a, result_b;
  integer checks, errors, i;
  logic [31:0] state;
  ibex_alu_sub32 ref_a(.operand_a_i(operand_a_i), .operand_b_i(operand_b_i), .result_o(result_a));
  ibex_alu_ltu32 ref_b(.operand_a_i(b_operand_a_i), .operand_b_i(b_operand_b_i), .result_o(result_b));
  ibex_fused_sub_ltu32_operand_mux dut(.operand_a_i(operand_a_i), .operand_b_i(operand_b_i), .b_operand_a_i(b_operand_a_i), .b_operand_b_i(b_operand_b_i),
                         .source_select_i(source_select_i), .result_o(result_o));
  function automatic [31:0] model_a(input [31:0] x, input [31:0] y);
    model_a = x - y;
  endfunction
  function automatic [31:0] model_b(input [31:0] x, input [31:0] y);
    model_b = {31'b0, (x < y)};
  endfunction
  function automatic [31:0] next_rand(input [31:0] x);
    begin next_rand = x ^ (x << 13); next_rand = next_rand ^ (next_rand >> 17); next_rand = next_rand ^ (next_rand << 5); end
  endfunction
  task automatic check(input integer op, input [31:0] x, input [31:0] y);
    begin
      operand_a_i=x; operand_b_i=y; b_operand_a_i=x ^ 32'h13579bdf; b_operand_b_i=y ^ 32'h2468ace0;
      source_select_i=1'b1; #1; checks=checks+1;
      if (result_o !== result_a || result_o !== model_a(x,y)) begin errors=errors+1; $display("TEST_ERROR sub "); end
      source_select_i=1'b0; #1; checks=checks+1;
      if (result_o !== result_b || result_o !== model_b(b_operand_a_i,b_operand_b_i)) begin errors=errors+1; $display("TEST_ERROR ltu "); end
    end
  endtask
  initial begin
    checks=0; errors=0; state=32'h1;
    check(0,32'h00000000,32'h00000000); check(0,32'hffffffff,32'h00000000);
    check(0,32'h80000000,32'h00000000); check(0,32'h7fffffff,32'h80000000);
    check(0,32'hffffffff,32'hffffffff); check(0,32'h00000001,32'hffffffff);
    for (i=0;i<256;i=i+1) begin state=next_rand(state); check(0,state,next_rand(state)); end
    if (errors != 0) $fatal(1,"Ibex fused regression failed");
    $display("IBEX_FUSION_TEST_PASS checks=%0d vectors=%0d operations=2",checks,checks/2); $finish;
  end
endmodule
