`timescale 1ns/1ps
module demo_op_slt #(parameter int WIDTH = 32) (
  input logic [WIDTH-1:0] a_i, b_i,
  output logic [WIDTH-1:0] result_o
);
  always_comb begin
    result_o = '0;
    result_o[0] = $signed(a_i) < $signed(b_i);
  end
endmodule
