`timescale 1ns/1ps

// Literal operator core: no registers, protocol, or wrapper logic.
module core_mul_u32 (
  input  logic [31:0] a_i,
  input  logic [31:0] b_i,
  output logic [63:0] result_o
);
  assign result_o = a_i * b_i;
endmodule
