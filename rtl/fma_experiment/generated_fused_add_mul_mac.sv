`timescale 1ns/1ps

// GENERATED SPECIALIZED REALIZATION: parameters and allowed modes are derived
// from the checked graph A/B, match, and structural-merge evidence.
module generated_fused_add_mul_mac (
  input logic clk_i, input logic rst_ni, input logic valid_i,
  input logic [1:0] mode_i, input logic [31:0] a_i, input logic [31:0] b_i,
  input logic [31:0] c_i, output logic ready_o, output logic valid_o,
  output logic [63:0] result_o
);
  localparam logic [1:0] MODE_ADD = 2'b00;
  localparam logic [1:0] MODE_MUL = 2'b01;
  localparam logic [1:0] MODE_MAC = 2'b10;
  logic [63:0] product, add_input, add_result, selected_result;
  assign ready_o = rst_ni;
  assign product = a_i * b_i;
  assign add_input = (mode_i == MODE_MAC) ? product : {32'b0, a_i};
  assign add_result = add_input + ((mode_i == MODE_MAC) ? {32'b0, c_i} : {32'b0, b_i});
  always_comb begin
    case (mode_i)
      MODE_ADD: selected_result = add_result;
      MODE_MUL: selected_result = product;
      MODE_MAC: selected_result = add_result;
      default: selected_result = '0;
    endcase
  end
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin valid_o <= 1'b0; result_o <= '0; end
    else begin valid_o <= valid_i; if (valid_i) result_o <= selected_result; end
  end
endmodule
