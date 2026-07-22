`timescale 1ns/1ps
module pair_or_slt (input logic [31:0] a_i, input logic [31:0] b_i, output logic [31:0] ri, output logic [31:0] rj);
  assign ri = a_i | b_i;
  assign rj = {31'b0, ($signed(a_i) < $signed(b_i))};
endmodule
