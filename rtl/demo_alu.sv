`timescale 1ns/1ps

// Small multi-operation functional unit used to demonstrate the framework.
// The operation encoding is part of this adapter's design contract.
module demo_alu #(
  parameter int WIDTH = 32
) (
  input  logic [WIDTH-1:0] a_i,
  input  logic [WIDTH-1:0] b_i,
  input  logic [2:0]       op_i,
  output logic [WIDTH-1:0] result_o
);
  localparam int SHAMT_WIDTH = (WIDTH > 1) ? $clog2(WIDTH) : 1;

  localparam logic [2:0] OP_ADD = 3'd0;
  localparam logic [2:0] OP_SUB = 3'd1;
  localparam logic [2:0] OP_AND = 3'd2;
  localparam logic [2:0] OP_OR  = 3'd3;
  localparam logic [2:0] OP_XOR = 3'd4;
  localparam logic [2:0] OP_SLT = 3'd5;
  localparam logic [2:0] OP_SLL = 3'd6;

  always_comb begin
    result_o = '0;
    unique case (op_i)
      OP_ADD: result_o = a_i + b_i;
      OP_SUB: result_o = a_i - b_i;
      OP_AND: result_o = a_i & b_i;
      OP_OR : result_o = a_i | b_i;
      OP_XOR: result_o = a_i ^ b_i;
      OP_SLT: result_o = {{(WIDTH-1){1'b0}}, ($signed(a_i) < $signed(b_i))};
      OP_SLL: result_o = (WIDTH > 1) ? (a_i << b_i[SHAMT_WIDTH-1:0]) : a_i;
      default: result_o = '0;
    endcase
  end

endmodule
