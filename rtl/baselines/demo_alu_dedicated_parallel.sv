`timescale 1ns/1ps

// Throughput reference: every operation is available concurrently. Its area
// must be compared with capacity normalization, not directly declared worse.
module demo_alu_dedicated_parallel #(
  parameter int WIDTH = 32
) (
  input logic [WIDTH-1:0] a_i,
  input logic [WIDTH-1:0] b_i,
  output logic [WIDTH-1:0] add_o,
  output logic [WIDTH-1:0] sub_o,
  output logic [WIDTH-1:0] and_o,
  output logic [WIDTH-1:0] or_o,
  output logic [WIDTH-1:0] xor_o,
  output logic [WIDTH-1:0] slt_o,
  output logic [WIDTH-1:0] sll_o
);
  localparam int SHAMT_WIDTH = (WIDTH > 1) ? $clog2(WIDTH) : 1;
  demo_op_add #(.WIDTH(WIDTH)) u_add (.a_i(a_i), .b_i(b_i), .result_o(add_o));
  demo_op_sub #(.WIDTH(WIDTH)) u_sub (.a_i(a_i), .b_i(b_i), .result_o(sub_o));
  demo_op_and #(.WIDTH(WIDTH)) u_and (.a_i(a_i), .b_i(b_i), .result_o(and_o));
  demo_op_or  #(.WIDTH(WIDTH)) u_or  (.a_i(a_i), .b_i(b_i), .result_o(or_o));
  demo_op_xor #(.WIDTH(WIDTH)) u_xor (.a_i(a_i), .b_i(b_i), .result_o(xor_o));
  demo_op_slt #(.WIDTH(WIDTH)) u_slt (.a_i(a_i), .b_i(b_i), .result_o(slt_o));
  demo_op_sll #(.WIDTH(WIDTH)) u_sll
    (.a_i(a_i), .shamt_i(b_i[SHAMT_WIDTH-1:0]), .result_o(sll_o));
endmodule
