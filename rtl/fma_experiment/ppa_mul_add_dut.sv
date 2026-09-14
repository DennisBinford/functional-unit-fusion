`timescale 1ns/1ps

// Compile-time selection gives every PPA candidate exactly the same top-level
// interface and VCD scope without adding a comparison-only wrapper mux.
module ppa_mul_add_dut (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        valid_i,
  input  logic        add_i,
  input  logic [31:0] a_i,
  input  logic [31:0] b_i,
  output logic        ready_o,
  output logic        valid_o,
  output logic [63:0] result_o
);
`ifdef PPA_PARALLEL
  registered_parallel_mul_add candidate (.*);
`elsif PPA_RADIX2_ORIGINAL
  shared_iterative_mul_add candidate (.*);
`elsif PPA_RADIX2_REUSED
  shared_reused_adder_mul_add candidate (.*);
`elsif PPA_RADIX4_REUSED
  radix4_reused_adder_mul_add candidate (.*);
`else
  initial $error("Select exactly one PPA candidate define");
`endif
endmodule
