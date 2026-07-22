`timescale 1ns/1ps
module demo_op_add #(parameter int WIDTH = 32) (
  input logic [WIDTH-1:0] a_i, b_i,
  output logic [WIDTH-1:0] result_o
);
  always_comb result_o = a_i + b_i;
endmodule
