`timescale 1ns/1ps

// Integer fused multiply-add candidate. The expression is intentionally kept
// as one arithmetic datapath so synthesis can choose the implementation of
// the multiply-plus-add operation. The output is the full 2*WIDTH-bit product
// plus a sign-extended WIDTH-bit addend.
module fused_mul_add #(
  parameter int WIDTH = 32
) (
  input logic [WIDTH-1:0]    a_i,
  input logic [WIDTH-1:0]    b_i,
  input logic [WIDTH-1:0]    c_i,
  output logic [2*WIDTH-1:0] result_o
);
  logic [2*WIDTH-1:0] product;
  logic [2*WIDTH-1:0] addend;

  assign product = a_i * b_i;
  assign addend = {{WIDTH{c_i[WIDTH-1]}}, c_i};
  assign result_o = product + addend;
endmodule
