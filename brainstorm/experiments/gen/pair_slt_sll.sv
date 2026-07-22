`timescale 1ns/1ps
module pair_slt_sll (input logic [31:0] a_i, input logic [31:0] b_i, output logic [31:0] ri, output logic [31:0] rj);
  assign ri = {31'b0, ($signed(a_i) < $signed(b_i))};
  assign rj = a_i << b_i[4:0];
endmodule
