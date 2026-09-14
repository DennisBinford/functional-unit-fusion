`timescale 1ns/1ps

// Fair-interface baseline: independent combinational ADD and MUL datapaths
// feed one output register. A request may be accepted every cycle.
module registered_parallel_mul_add #(
  parameter int WIDTH = 32
) (
  input  logic               clk_i,
  input  logic               rst_ni,
  input  logic               valid_i,
  input  logic               add_i,
  input  logic [WIDTH-1:0]   a_i,
  input  logic [WIDTH-1:0]   b_i,
  output logic               ready_o,
  output logic               valid_o,
  output logic [2*WIDTH-1:0] result_o
);
  logic [WIDTH-1:0] sum;
  logic [2*WIDTH-1:0] product;

  assign ready_o = rst_ni;
  assign sum = a_i + b_i;
  assign product = a_i * b_i;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      valid_o <= 1'b0;
      result_o <= '0;
    end else begin
      valid_o <= valid_i;
      if (valid_i)
        result_o <= add_i ? {{WIDTH{sum[WIDTH-1]}}, sum} : product;
    end
  end
endmodule
