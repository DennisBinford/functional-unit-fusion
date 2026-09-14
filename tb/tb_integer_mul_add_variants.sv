`timescale 1ns/1ps

module tb_integer_mul_add_variants;
  localparam int WIDTH = 8;
  logic clk_i, rst_ni = 0;
  logic valid_i = 0, add_i = 0;
  logic [WIDTH-1:0] a_i = 0, b_i = 0, c_i = 0;
  logic [2*WIDTH-1:0] separate_result, fused_result, scheduled_result, shared_result;
  logic scheduled_valid, shared_ready, shared_valid;
  integer checks = 0, errors = 0;
  always #5 clk_i = ~clk_i;

  separate_mul_add #(.WIDTH(WIDTH)) separate (.*,.result_o(separate_result));
  fused_mul_add #(.WIDTH(WIDTH)) fused (.*,.result_o(fused_result));
  scheduled_mul_add #(.WIDTH(WIDTH)) scheduled (.*,.valid_o(scheduled_valid),.result_o(scheduled_result));
  shared_iterative_mul_add #(.WIDTH(WIDTH)) shared (.*,.ready_o(shared_ready),.valid_o(shared_valid),.result_o(shared_result));

  task automatic check_case(input logic [WIDTH-1:0] a, input logic [WIDTH-1:0] b, input logic is_add);
    logic [WIDTH-1:0] add_result;
    logic [2*WIDTH-1:0] expected, fused_expected;
    begin
      if (!shared_ready) begin
        errors=errors+1; $display("SHARED_NOT_READY a=%0d b=%0d add=%b",a,b,is_add);
      end
      add_result = WIDTH'(a+b);
      expected = is_add ? {{WIDTH{add_result[WIDTH-1]}}, add_result} : a*b;
      fused_expected = a*b + {{WIDTH{b[WIDTH-1]}}, b};
      @(negedge clk_i); a_i=a; b_i=b; c_i=b; add_i=is_add; valid_i=1;
      #1;
      if (separate_result !== expected || fused_result !== fused_expected) begin
        errors=errors+1; $display("COMB_ERROR a=%0d b=%0d add=%b",a,b,is_add);
      end
      @(negedge clk_i); valid_i=0; add_i=0;
      if (!scheduled_valid || scheduled_result !== expected) begin
        errors=errors+1; $display("SCHED_ERROR a=%0d b=%0d add=%b",a,b,is_add);
      end
      if (is_add) begin
        if (!shared_valid || shared_result !== expected) begin
          errors=errors+1; $display("SHARED_ADD_ERROR a=%0d b=%0d",a,b);
        end
      end else begin
        repeat (WIDTH) @(negedge clk_i);
        if (!shared_valid || shared_result !== expected) begin
          errors=errors+1; $display("SHARED_MUL_ERROR a=%0d b=%0d",a,b);
        end
      end
      checks=checks+1;
    end
  endtask

  initial begin
    clk_i=0;
    repeat(2) @(negedge clk_i); rst_ni=1;
    check_case(0,0,0); check_case(13,11,0); check_case(255,3,0);
    check_case(0,0,1); check_case(7,5,1); check_case(255,2,1);
    for (integer i=0; i<64; i=i+1)
      check_case(WIDTH'((i*37)+11), WIDTH'((i*19)+3), i[0]);
    if (errors != 0) begin $display("INTEGER_FMA_TEST_FAIL checks=%0d errors=%0d",checks,errors); $fatal(1); end
    $display("INTEGER_FMA_TEST_PASS checks=%0d",checks); $finish;
  end
endmodule
