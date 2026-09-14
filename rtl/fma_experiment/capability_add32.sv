`timescale 1ns/1ps

module capability_add32 (
  input  logic [31:0] a_i,
  input  logic [31:0] b_i,
  output logic [31:0] result_o
);
  assign result_o = a_i + b_i;
endmodule
