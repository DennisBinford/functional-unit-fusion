`timescale 1ns/1ps

module capability_add64 (
  input  logic [63:0] a_i,
  input  logic [63:0] b_i,
  output logic [63:0] result_o
);
  assign result_o = a_i + b_i;
endmodule
