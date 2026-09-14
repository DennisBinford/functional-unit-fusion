`timescale 1ns/1ps

// Combinational fused operator core for the literal area comparison.
// There are no registers, ready/valid signals, or interface wrapper cells.
module core_fused_add_mul_mac_u32 (
  input  logic [1:0]  mode_i,       // 00=ADD, 01=MUL, 10=MAC
  input  logic [31:0] a_i,
  input  logic [31:0] b_i,
  input  logic [31:0] c_i,
  output logic [63:0] result_o
);
  localparam logic [1:0] MODE_ADD = 2'b00;
  localparam logic [1:0] MODE_MUL = 2'b01;
  localparam logic [1:0] MODE_MAC = 2'b10;
  logic [63:0] product;
  logic [63:0] add_input;
  logic [63:0] add_result;

  assign product = a_i * b_i;
  assign add_input = (mode_i == MODE_MAC) ? product : {32'b0, a_i};
  assign add_result = add_input + ((mode_i == MODE_MAC) ? {32'b0, c_i} : {32'b0, b_i});
  always_comb begin
    case (mode_i)
      MODE_ADD: result_o = add_result;
      MODE_MUL: result_o = product;
      MODE_MAC: result_o = add_result;
      default:  result_o = '0;
    endcase
  end
endmodule
