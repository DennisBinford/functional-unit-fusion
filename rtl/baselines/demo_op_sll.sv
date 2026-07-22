`timescale 1ns/1ps
module demo_op_sll #(
  parameter int WIDTH = 32,
  parameter int SHAMT_WIDTH = (WIDTH > 1) ? $clog2(WIDTH) : 1
) (
  input logic [WIDTH-1:0] a_i,
  input logic [SHAMT_WIDTH-1:0] shamt_i,
  output logic [WIDTH-1:0] result_o
);
  always_comb result_o = (WIDTH > 1) ? (a_i << shamt_i) : a_i;
endmodule
