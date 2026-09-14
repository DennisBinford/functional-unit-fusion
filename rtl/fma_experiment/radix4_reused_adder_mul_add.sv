`timescale 1ns/1ps

// Two-bit-per-cycle unsigned multiplier. The accumulator adder also performs
// standalone ADD; a second adder forms 3*multiplicand for radix-4 digit 3.
// This explores a faster point between the 32-cycle shared design and the
// one-cycle fully parallel multiplier.
module radix4_reused_adder_mul_add #(
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
  localparam int STEPS = (WIDTH + 1) / 2;
  localparam int COUNT_WIDTH = $clog2(STEPS + 1);
  localparam logic [COUNT_WIDTH-1:0] LAST_COUNT = COUNT_WIDTH'(STEPS - 1);

  logic busy_q;
  logic [WIDTH-1:0] multiplier_q;
  logic [2*WIDTH-1:0] multiplicand_q;
  logic [2*WIDTH-1:0] accumulator_q;
  logic [COUNT_WIDTH-1:0] count_q;
  logic [2*WIDTH-1:0] triple_multiplicand;
  logic [2*WIDTH-1:0] digit_addend;
  logic [2*WIDTH-1:0] adder_lhs;
  logic [2*WIDTH-1:0] adder_rhs;
  logic [2*WIDTH-1:0] adder_result;

  assign ready_o = !busy_q;
  assign triple_multiplicand = multiplicand_q + (multiplicand_q << 1);

  always_comb begin
    unique case (multiplier_q[1:0])
      2'b00: digit_addend = '0;
      2'b01: digit_addend = multiplicand_q;
      2'b10: digit_addend = multiplicand_q << 1;
      default: digit_addend = triple_multiplicand;
    endcase
  end

  assign adder_lhs = busy_q ? accumulator_q : {{WIDTH{1'b0}}, a_i};
  assign adder_rhs = busy_q ? digit_addend : {{WIDTH{1'b0}}, b_i};
  assign adder_result = adder_lhs + adder_rhs;

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
          result_o <= {{WIDTH{adder_result[WIDTH-1]}}, adder_result[WIDTH-1:0]};
          valid_o <= 1'b1;
        end else begin
          busy_q <= 1'b1;
          multiplier_q <= b_i;
          multiplicand_q <= {{WIDTH{1'b0}}, a_i};
          accumulator_q <= '0;
          count_q <= '0;
        end
      end else if (busy_q) begin
        accumulator_q <= adder_result;
        multiplier_q <= multiplier_q >> 2;
        multiplicand_q <= multiplicand_q << 2;
        if (count_q == LAST_COUNT) begin
          result_o <= adder_result;
          valid_o <= 1'b1;
          busy_q <= 1'b0;
        end else begin
          count_q <= count_q + 1'b1;
        end
      end
    end
  end
endmodule
