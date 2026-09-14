`timescale 1ns/1ps

// Matched triple-mode parallel baseline. ADD and MUL remain separate; MAC
// uses an additional final adder so the comparison exposes sharing overhead.
module parallel_add_mul_mac_baseline (
  input logic clk_i, input logic rst_ni, input logic valid_i,
  input logic [1:0] mode_i, input logic [31:0] a_i, input logic [31:0] b_i,
  input logic [31:0] c_i, output logic ready_o, output logic valid_o,
  output logic [63:0] result_o
);
  localparam logic [1:0] MODE_ADD = 2'b00;
  localparam logic [1:0] MODE_MUL = 2'b01;
  localparam logic [1:0] MODE_MAC = 2'b10;
  logic [63:0] product, add_result, mac_result;
  assign ready_o = rst_ni;
  assign product = a_i * b_i;
  assign add_result = {32'b0, a_i} + {32'b0, b_i};
  assign mac_result = product + {32'b0, c_i};
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin valid_o <= 1'b0; result_o <= '0; end
    else begin
      valid_o <= valid_i;
      if (valid_i) begin
        case (mode_i)
          MODE_ADD: result_o <= add_result;
          MODE_MUL: result_o <= product;
          MODE_MAC: result_o <= mac_result;
          default: result_o <= '0;
        endcase
      end
    end
  end
endmodule
