`timescale 1ns/1ps
module tb_independent_input_fused;
  logic [31:0] operand_a_i, operand_b_i, b_operand_a_i, b_operand_b_i;
  logic source_select_i;
  logic [31:0] result_o, ref_sub_result, ref_ltu_result;
  integer checks, errors, i;
  logic [31:0] state;
  ibex_alu_sub32 ref_sub(.operand_a_i(operand_a_i), .operand_b_i(operand_b_i), .result_o(ref_sub_result));
  ibex_alu_ltu32 ref_ltu(.operand_a_i(b_operand_a_i), .operand_b_i(b_operand_b_i), .result_o(ref_ltu_result));
  ibex_fused_sub_ltu32 dut(.operand_a_i(operand_a_i), .operand_b_i(operand_b_i),
                         .b_operand_a_i(b_operand_a_i), .b_operand_b_i(b_operand_b_i),
                         .source_select_i(source_select_i), .result_o(result_o));
  function automatic [31:0] next_rand(input [31:0] x);
    begin next_rand = x ^ (x << 13); next_rand = next_rand ^ (next_rand >> 17); next_rand = next_rand ^ (next_rand << 5); end
  endfunction
  task automatic check(input [31:0] x, input [31:0] y);
    begin
      operand_a_i=x; operand_b_i=y;
      b_operand_a_i=x ^ 32'h13579bdf; b_operand_b_i=y ^ 32'h2468ace0;
      source_select_i=1'b1; #1; checks=checks+1;
      if (result_o !== ref_sub_result || result_o !== (x-y)) errors=errors+1;
      source_select_i=1'b0; #1; checks=checks+1;
      if (result_o !== ref_ltu_result || result_o !== {31'b0,((b_operand_a_i < b_operand_b_i))}) errors=errors+1;
    end
  endtask
  initial begin
    checks=0; errors=0; state=32'h1;
    check(32'h00000000,32'h00000000); check(32'hffffffff,32'h00000000);
    check(32'h80000000,32'h00000000); check(32'h7fffffff,32'h80000000);
    check(32'hffffffff,32'hffffffff); check(32'h00000001,32'hffffffff);
    for (i=0;i<256;i=i+1) begin state=next_rand(state); check(state,next_rand(state)); end
    if (errors != 0) $fatal(1,"interface control regression failed errors=%0d", errors);
    $display("INTERFACE_CONTROL_TEST_PASS case=independent_input_fused checks=%0d vectors=%0d", checks, checks/2); $finish;
  end
endmodule
