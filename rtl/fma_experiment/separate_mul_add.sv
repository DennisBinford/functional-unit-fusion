`timescale 1ns/1ps

// Integer baseline: multiplication and addition are physically separate
// datapaths and the selected result is muxed at the output.
module separate_mul_add #(
  parameter int WIDTH = 32
) (
  input logic [WIDTH-1:0] a_i,
  input logic [WIDTH-1:0] b_i,
  input logic             add_i,
  output logic [2*WIDTH-1:0] result_o
);
  logic [2*WIDTH-1:0] product;
  logic [WIDTH-1:0] sum;

  assign product = a_i * b_i;
  assign sum = a_i + b_i;

  always_comb begin
    result_o = add_i ? {{WIDTH{sum[WIDTH-1]}}, sum} : product;
  end
endmodule
