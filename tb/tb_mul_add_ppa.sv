`timescale 1ns/1ps

module tb_mul_add_ppa;
  localparam int WIDTH = 32;
  logic clk_i;
  logic rst_ni = 1'b0;
  logic valid_i = 1'b0;
  logic add_i = 1'b0;
  logic [WIDTH-1:0] a_i = '0;
  logic [WIDTH-1:0] b_i = '0;
  logic ready_o;
  logic valid_o;
  logic [2*WIDTH-1:0] result_o;
  integer checks = 0;
  integer cycles;

  ppa_mul_add_dut dut (.*);

  initial begin
    clk_i = 1'b0;
    forever #5 clk_i = ~clk_i;
  end

  always @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)
      cycles <= 0;
    else
      cycles <= cycles + 1;
  end

  task automatic transact(
    input logic [WIDTH-1:0] a,
    input logic [WIDTH-1:0] b,
    input logic add_operation
  );
    logic [WIDTH-1:0] sum;
    logic [2*WIDTH-1:0] expected;
    begin
      while (!ready_o) @(negedge clk_i);
      a_i = a;
      b_i = b;
      add_i = add_operation;
      valid_i = 1'b1;
      @(negedge clk_i);
      valid_i = 1'b0;
      sum = a + b;
      expected = add_operation ? {{WIDTH{sum[WIDTH-1]}}, sum} : a * b;
      while (!valid_o) @(negedge clk_i);
      if (result_o !== expected)
        $fatal(1, "mismatch add=%0d a=%h b=%h expected=%h got=%h",
               add_operation, a, b, expected, result_o);
      checks = checks + 1;
    end
  endtask

  initial begin
    $dumpfile("activity.vcd");
    $dumpvars(0, dut);
    repeat (3) @(negedge clk_i);
    rst_ni = 1'b1;

    transact(32'h00000000, 32'h00000000, 1'b0);
    transact(32'h0000000d, 32'h0000000b, 1'b0);
    transact(32'hffffffff, 32'h00000003, 1'b0);
    transact(32'h00000000, 32'h00000000, 1'b1);
    transact(32'h00000007, 32'h00000005, 1'b1);
    transact(32'hffffffff, 32'h00000002, 1'b1);
    for (integer i = 0; i < 64; i = i + 1)
      transact(WIDTH'((i * 37) + 11), WIDTH'((i * 19) + 3), i[0]);

    repeat (2) @(negedge clk_i);
    $display("PPA_MUL_ADD_TEST_PASS checks=%0d cycles=%0d", checks, cycles);
    $finish;
  end
endmodule
