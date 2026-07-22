`timescale 1ns/1ps

// Fair no-cross-operation-sharing baseline. Each leaf remains independently
// optimizable, while keep_hierarchy prevents Yosys from merging leaf logic.
module demo_alu_separate_locked #(
  parameter int WIDTH = 32
) (
  input  logic [WIDTH-1:0] a_i,
  input  logic [WIDTH-1:0] b_i,
  input  logic [2:0]       op_i,
  output logic [WIDTH-1:0] result_o
);
  localparam int SHAMT_WIDTH = (WIDTH > 1) ? $clog2(WIDTH) : 1;
  logic [WIDTH-1:0] add_result, sub_result, and_result, or_result;
  logic [WIDTH-1:0] xor_result, slt_result, sll_result;

  (* keep_hierarchy = 1 *) demo_op_add #(.WIDTH(WIDTH)) u_add
    (.a_i(a_i), .b_i(b_i), .result_o(add_result));
  (* keep_hierarchy = 1 *) demo_op_sub #(.WIDTH(WIDTH)) u_sub
    (.a_i(a_i), .b_i(b_i), .result_o(sub_result));
  (* keep_hierarchy = 1 *) demo_op_and #(.WIDTH(WIDTH)) u_and
    (.a_i(a_i), .b_i(b_i), .result_o(and_result));
  (* keep_hierarchy = 1 *) demo_op_or #(.WIDTH(WIDTH)) u_or
    (.a_i(a_i), .b_i(b_i), .result_o(or_result));
  (* keep_hierarchy = 1 *) demo_op_xor #(.WIDTH(WIDTH)) u_xor
    (.a_i(a_i), .b_i(b_i), .result_o(xor_result));
  (* keep_hierarchy = 1 *) demo_op_slt #(.WIDTH(WIDTH)) u_slt
    (.a_i(a_i), .b_i(b_i), .result_o(slt_result));
  (* keep_hierarchy = 1 *) demo_op_sll #(.WIDTH(WIDTH)) u_sll
    (.a_i(a_i), .shamt_i(b_i[SHAMT_WIDTH-1:0]), .result_o(sll_result));

  always_comb begin
    result_o = '0;
    unique case (op_i)
      3'd0: result_o = add_result;
      3'd1: result_o = sub_result;
      3'd2: result_o = and_result;
      3'd3: result_o = or_result;
      3'd4: result_o = xor_result;
      3'd5: result_o = slt_result;
      3'd6: result_o = sll_result;
      default: result_o = '0;
    endcase
  end
endmodule
