`timescale 1ns/1ps

// Deliberately fused candidate. ADD, SUB, and signed SLT share one adder/carry
// path; the remaining operations retain direct combinational structures.
module demo_alu_manual_fused #(
  parameter int WIDTH = 32
) (
  input  logic [WIDTH-1:0] a_i,
  input  logic [WIDTH-1:0] b_i,
  input  logic [2:0]       op_i,
  output logic [WIDTH-1:0] result_o
);
  localparam int SHAMT_WIDTH = (WIDTH > 1) ? $clog2(WIDTH) : 1;
  logic                    subtract_mode;
  logic [WIDTH-1:0]        adder_b;
  logic [WIDTH-1:0]        arithmetic_result;
  logic                    subtract_overflow;
  logic                    signed_less_than;

  always_comb begin
    subtract_mode = (op_i == 3'd1) || (op_i == 3'd5);
    adder_b = b_i ^ {WIDTH{subtract_mode}};
    arithmetic_result = a_i + adder_b
                      + {{(WIDTH-1){1'b0}}, subtract_mode};
    subtract_overflow = (a_i[WIDTH-1] ^ b_i[WIDTH-1])
                      & (arithmetic_result[WIDTH-1] ^ a_i[WIDTH-1]);
    signed_less_than = arithmetic_result[WIDTH-1] ^ subtract_overflow;

    result_o = '0;
    unique case (op_i)
      3'd0, 3'd1: result_o = arithmetic_result;
      3'd2: result_o = a_i & b_i;
      3'd3: result_o = a_i | b_i;
      3'd4: result_o = a_i ^ b_i;
      3'd5: begin
        result_o = '0;
        result_o[0] = signed_less_than;
      end
      3'd6: result_o = (WIDTH > 1) ? (a_i << b_i[SHAMT_WIDTH-1:0]) : a_i;
      default: result_o = '0;
    endcase
  end
endmodule
