`timescale 1ns/1ps

// A second RTL-derived ADD/MUL selector used only as graph eligibility
// evidence. The generated result is a graph-gated specialized RTL template;
// this candidate does not itself encode c_i or MAC semantics.
module graph_candidate_add_mul (
  input logic [31:0] a_i,
  input logic [31:0] b_i,
  input logic        add_i,
  output logic [63:0] result_o
);
  logic [63:0] product;
  logic [31:0] sum;
  assign product = a_i * b_i;
  assign sum = a_i + b_i;
  assign result_o = add_i ? {{32{sum[31]}}, sum} : product;
endmodule
