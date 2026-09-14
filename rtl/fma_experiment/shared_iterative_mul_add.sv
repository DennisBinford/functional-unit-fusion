`timescale 1ns/1ps

// Shared integer baseline. One WIDTH+1-bit adder/accumulator is reused for
// both operations: ADD completes in one cycle; MUL uses shift-and-add over
// WIDTH cycles. This is intentionally a time-multiplexed unit, so the area
// advantage is evaluated together with its latency and initiation interval.
module shared_iterative_mul_add #(
  parameter int WIDTH = 32
) (
  input  logic                clk_i,
  input  logic                rst_ni,
  input  logic                valid_i,
  input  logic                add_i,
  input  logic [WIDTH-1:0]    a_i,
  input  logic [WIDTH-1:0]    b_i,
  output logic                ready_o,
  output logic                valid_o,
  output logic [2*WIDTH-1:0]  result_o
);
  localparam int COUNT_WIDTH = $clog2(WIDTH + 1);
  localparam logic [COUNT_WIDTH-1:0] LAST_COUNT = COUNT_WIDTH'(WIDTH - 1);
  logic busy_q;
  logic [WIDTH-1:0] multiplier_q;
  logic [2*WIDTH-1:0] multiplicand_q;
  logic [2*WIDTH-1:0] accumulator_q;
  logic [COUNT_WIDTH-1:0] count_q;
  logic [2*WIDTH-1:0] addend;
  logic [2*WIDTH-1:0] next_accumulator;
  logic [WIDTH:0] sum_ext;

  assign ready_o = !busy_q;
  assign addend = multiplier_q[0] ? multiplicand_q : '0;
  assign next_accumulator = accumulator_q + addend;
  assign sum_ext = {1'b0, a_i} + {1'b0, b_i};

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      busy_q <= 1'b0;
      multiplier_q <= '0;
      multiplicand_q <= '0;
      accumulator_q <= '0;
      count_q <= '0;
      valid_o <= 1'b0;
      result_o <= '0;
    end else begin
      valid_o <= 1'b0;

      if (!busy_q && valid_i) begin
        if (add_i) begin
          result_o <= {{WIDTH{sum_ext[WIDTH-1]}}, sum_ext[WIDTH-1:0]};
          valid_o <= 1'b1;
        end else begin
          busy_q <= 1'b1;
          multiplier_q <= b_i;
          multiplicand_q <= {{WIDTH{1'b0}}, a_i};
          accumulator_q <= '0;
          count_q <= '0;
        end
      end else if (busy_q) begin
        accumulator_q <= next_accumulator;
        multiplier_q <= multiplier_q >> 1;
        multiplicand_q <= multiplicand_q << 1;
        if (count_q == LAST_COUNT) begin
          result_o <= next_accumulator;
          valid_o <= 1'b1;
          busy_q <= 1'b0;
        end else begin
          count_q <= count_q + 1'b1;
        end
      end
    end
  end
endmodule
