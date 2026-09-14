`timescale 1ns/1ps

// Integer scheduled baseline: one request is accepted at a time and the
// operation is selected before the arithmetic stage. This makes the sharing
// and latency assumption explicit; synthesis must not be interpreted as proof
// that multiplication and addition use the same physical hardware.
module scheduled_mul_add #(
  parameter int WIDTH = 32
) (
  input logic                 clk_i,
  input logic                 rst_ni,
  input logic                 valid_i,
  input logic                 add_i,
  input logic [WIDTH-1:0]     a_i,
  input logic [WIDTH-1:0]     b_i,
  output logic                valid_o,
  output logic [2*WIDTH-1:0]  result_o
);
  logic [WIDTH:0] sum_ext;

  assign sum_ext = {1'b0, a_i} + {1'b0, b_i};

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      valid_o <= 1'b0;
      result_o <= '0;
    end else begin
      valid_o <= valid_i;
      if (valid_i) begin
        if (add_i)
          result_o <= {{WIDTH{sum_ext[WIDTH-1]}}, sum_ext[WIDTH-1:0]};
        else
          result_o <= a_i * b_i;
      end
    end
  end
endmodule
