`timescale 1ns/1ps

// Local integration wrapper for the Apache-2.0 Tachyum FMA reference.
// The reference packs two binary32 lanes into its 64-bit interface; this
// wrapper uses lane 0 and exposes a single 32-bit result.
module tachyum_fma_single_wrapper (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        valid_i,
  input  logic [31:0] multiplicand_i,
  input  logic [31:0] multiplier_i,
  input  logic [31:0] addend_i,
  output logic        valid_o,
  output logic [31:0] result_o,
  output logic        invalid_o,
  output logic        overflow_o,
  output logic        underflow_o,
  output logic        inexact_o
);
  logic [3:0] invalid_operation;
  logic [3:0] overflow;
  logic [3:0] underflow;
  logic [3:0] inexact_result;
  logic [3:0] denormal_input;
  logic [3:0] valid_pipe;
  logic [63:0] reference_result;

  f_mul_add reference (
    .result(reference_result),
    .invalid_operation(invalid_operation),
    .overflow(overflow),
    .underflow(underflow),
    .inexact_result(inexact_result),
    .denormal_input(denormal_input),
    .multiplicand({32'b0, multiplicand_i}),
    .multiplier({32'b0, multiplier_i}),
    .addend({32'b0, addend_i}),
    .vfmul64(1'b0),
    .vfmul32(1'b1),
    .vfmul16(1'b0),
    .use_multiplier(1'b1),
    .use_addend(1'b1),
    .negate_a(1'b0),
    .negate_c(1'b0),
    .rounding_mode(2'b00),
    .data_valid(valid_i),
    .treat_denormal_inputs_as_zero(1'b0),
    .force_denormal_outputs_to_zero(1'b0),
    .clk(clk_i),
    .reset_n(rst_ni)
  );

  // f_mul_add documents a four-cycle data path. The wrapper makes that
  // contract observable to the local testbench.
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)
      valid_pipe <= '0;
    else
      valid_pipe <= {valid_pipe[2:0], valid_i};
  end

  assign valid_o = valid_pipe[3];
  assign result_o = reference_result[31:0];
  assign invalid_o = invalid_operation[0];
  assign overflow_o = overflow[0];
  assign underflow_o = underflow[0];
  assign inexact_o = inexact_result[0];
endmodule
