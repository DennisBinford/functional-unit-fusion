`timescale 1ns/1ps
module pair_add_xor (input logic [31:0] a_i, input logic [31:0] b_i, output logic [31:0] ri, output logic [31:0] rj);
  assign ri = a_i + b_i;
  assign rj = a_i ^ b_i;
endmodule
