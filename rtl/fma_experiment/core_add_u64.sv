`timescale 1ns/1ps

// Canonical unsigned chained-adder core.  32-bit ADD operands are supplied
// zero-extended; the same unit also accepts a 64-bit multiplier result.
module core_add_u64 (
  input  logic [63:0] a_i,
  input  logic [63:0] b_i,
  output logic [63:0] result_o
);
  assign result_o = a_i + b_i;
endmodule
