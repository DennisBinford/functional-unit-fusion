`timescale 1ns/1ps

module tb_tachyum_fma_single;
  logic clk_i = 1'b0;
  logic rst_ni = 1'b0;
  logic valid_i = 1'b0;
  logic [31:0] multiplicand_i = '0;
  logic [31:0] multiplier_i = '0;
  logic [31:0] addend_i = '0;
  logic valid_o;
  logic [31:0] result_o;
  logic invalid_o, overflow_o, underflow_o, inexact_o;
  integer checks = 0;
  integer errors = 0;

  always #5 clk_i = ~clk_i;

  tachyum_fma_single_wrapper dut (.*);

  task automatic check_case(
    input logic [31:0] a,
    input logic [31:0] b,
    input logic [31:0] c,
    input logic [31:0] expected
  );
    begin
      @(negedge clk_i);
      multiplicand_i = a;
      multiplier_i = b;
      addend_i = c;
      valid_i = 1'b1;
      @(negedge clk_i);
      valid_i = 1'b0;
      repeat (3) @(negedge clk_i);
      checks = checks + 1;
      if (!valid_o || result_o !== expected) begin
        errors = errors + 1;
        $display("FMA_ERROR a=%h b=%h c=%h expected=%h actual=%h valid=%b",
                 a, b, c, expected, result_o, valid_o);
      end
    end
  endtask

  initial begin
    repeat (2) @(negedge clk_i);
    rst_ni = 1'b1;

    // 1.0*2.0+3.0 = 5.0
    check_case(32'h3f800000, 32'h40000000, 32'h40400000, 32'h40a00000);
    // 1.5*2.0+0.5 = 3.5
    check_case(32'h3fc00000, 32'h40000000, 32'h3f000000, 32'h40600000);
    // -2.0*4.0+1.0 = -7.0
    check_case(32'hc0000000, 32'h40800000, 32'h3f800000, 32'hc0e00000);
    // 0.0*anything+1.25 = 1.25
    check_case(32'h00000000, 32'h7f7fffff, 32'h3fa00000, 32'h3fa00000);

    if (errors != 0) begin
      $display("FMA_TEST_FAIL checks=%0d errors=%0d", checks, errors);
      $fatal(1);
    end
    $display("FMA_TEST_PASS checks=%0d", checks);
    $finish;
  end
endmodule
