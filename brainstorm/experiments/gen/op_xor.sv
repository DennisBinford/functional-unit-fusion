`timescale 1ns/1ps
module op_xor (input logic [31:0] a_i, input logic [31:0] b_i, output logic [31:0] r);
  assign r = a_i ^ b_i;
endmodule
