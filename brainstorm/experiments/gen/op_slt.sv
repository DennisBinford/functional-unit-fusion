`timescale 1ns/1ps
module op_slt (input logic [31:0] a_i, input logic [31:0] b_i, output logic [31:0] r);
  assign r = {31'b0, ($signed(a_i) < $signed(b_i))};
endmodule
