`timescale 1ns/1ps

module tb_shared_iterative_mul_add;
  localparam int WIDTH = 8;

  logic clk_i = 1'b0;
  logic rst_ni = 1'b0;
  logic valid_i = 1'b0;
  logic add_i = 1'b0;
  logic [WIDTH-1:0] a_i = '0;
  logic [WIDTH-1:0] b_i = '0;
  logic ready_o;
  logic valid_o;
  logic [2*WIDTH-1:0] result_o;
  integer checks = 0;
  integer errors = 0;

  always #5 clk_i = ~clk_i;

  shared_iterative_mul_add #(.WIDTH(WIDTH)) dut (.*);

  task automatic submit_and_check_add(
    input logic [WIDTH-1:0] a,
    input logic [WIDTH-1:0] b,
    input logic [2*WIDTH-1:0] expected
  );
    begin
      @(negedge clk_i);
      if (!ready_o) begin
        errors = errors + 1;
        $display("SHARED_ERROR ADD accepted while not ready");
      end
      a_i = a;
      b_i = b;
      add_i = 1'b1;
      valid_i = 1'b1;
      @(negedge clk_i);
      valid_i = 1'b0;
      add_i = 1'b0;
      if (!valid_o || result_o !== expected) begin
        errors = errors + 1;
        $display("SHARED_ERROR ADD a=%0d b=%0d expected=%0d actual=%0d valid=%b",
                 a, b, expected, result_o, valid_o);
      end
      checks = checks + 1;
    end
  endtask

  task automatic submit_and_check_mul(
    input logic [WIDTH-1:0] a,
    input logic [WIDTH-1:0] b,
    input logic [2*WIDTH-1:0] expected
  );
    begin
      @(negedge clk_i);
      if (!ready_o) begin
        errors = errors + 1;
        $display("SHARED_ERROR MUL accepted while not ready");
      end
      a_i = a;
      b_i = b;
      add_i = 1'b0;
      valid_i = 1'b1;
      @(negedge clk_i);
      valid_i = 1'b0;
      // WIDTH shift-and-add iterations follow the request cycle.
      repeat (WIDTH) @(negedge clk_i);
      if (!valid_o || result_o !== expected) begin
        errors = errors + 1;
        $display("SHARED_ERROR MUL a=%0d b=%0d expected=%0d actual=%0d valid=%b",
                 a, b, expected, result_o, valid_o);
      end
      checks = checks + 1;
    end
  endtask

  initial begin
    repeat (2) @(negedge clk_i);
    rst_ni = 1'b1;

    submit_and_check_add(8'd7, 8'd5, 16'd12);
    submit_and_check_mul(8'd13, 8'd11, 16'd143);
    submit_and_check_mul(8'd0, 8'd255, 16'd0);

    if (errors != 0) begin
      $display("SHARED_TEST_FAIL checks=%0d errors=%0d", checks, errors);
      $fatal(1);
    end
    $display("SHARED_TEST_PASS checks=%0d", checks);
    $finish;
  end
endmodule
