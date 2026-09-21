`timescale 1ns/1ps
module tb_intermediate_search;
  logic [31:0] b_operand_a_i, b_operand_b_i, operand_a_i, operand_b_i;
  logic source_select_i;
  logic [31:0] separate_result, fused_result;
  integer checks, errors, i;
  logic [31:0] state;
  ibex_lt_ge_separate separate(
    .b_operand_a_i, .b_operand_b_i, .operand_a_i, .operand_b_i,
    .source_select_i, .result_o(separate_result));
  ibex_fused_lt_ge32 fused(
    .b_operand_a_i, .b_operand_b_i, .operand_a_i, .operand_b_i,
    .source_select_i, .result_o(fused_result));
  function automatic [31:0] model_lt(input [31:0] x, input [31:0] y);
    model_lt = ($signed(x) < $signed(y)) ? 32'b1 : 32'b0;
  endfunction
  function automatic [31:0] model_ge(input [31:0] x, input [31:0] y);
    model_ge = ($signed(x) >= $signed(y)) ? 32'b1 : 32'b0;
  endfunction
  function automatic [31:0] next_rand(input [31:0] x);
    begin next_rand = x ^ (x << 13); next_rand = next_rand ^ (next_rand >> 17); next_rand = next_rand ^ (next_rand << 5); end
  endfunction
  task automatic check(input [31:0] x, input [31:0] y, input [31:0] u, input [31:0] v);
    begin
      operand_a_i=x; operand_b_i=y; b_operand_a_i=u; b_operand_b_i=v;
      source_select_i=1'b1; #1; checks=checks+1;
      if (fused_result !== separate_result || fused_result !== model_lt(x,y)) errors=errors+1;
      source_select_i=1'b0; #1; checks=checks+1;
      if (fused_result !== separate_result || fused_result !== model_ge(u,v)) errors=errors+1;
    end
  endtask
  initial begin
    checks=0; errors=0; state=32'h1;
    check(32'h0,32'h0,32'h0,32'h0);
    check(32'h80000000,32'h7fffffff,32'h7fffffff,32'h80000000);
    check(32'hffffffff,32'h1,32'h1,32'hffffffff);
    for (i=0;i<256;i=i+1) begin
      state=next_rand(state); check(state,next_rand(state),next_rand(next_rand(state)),next_rand(next_rand(next_rand(state))));
    end
    if (errors != 0) $fatal(1,"LT/GE equivalence failed: %0d", errors);
    $display("INTERMEDIATE_SEARCH_TEST_PASS checks=%0d vectors=%0d", checks, checks/2); $finish;
  end
endmodule
