`timescale 1ns/1ps

/* verilator lint_off SYNCASYNCNET */
module tb_interface_level_compare;
  logic clk_i, rst_ni, valid_i;
  logic [1:0] mode_i;
  logic [31:0] a_i, b_i, c_i;
  logic ready_g, valid_g;
  logic [63:0] result_g;
  logic ready_s, valid_s;
  logic [63:0] result_s;
  integer checks;
  integer cycles;

  generated_fused_add_mul_mac generated_ref(
    .clk_i, .rst_ni, .valid_i, .mode_i, .a_i, .b_i, .c_i,
    .ready_o(ready_g), .valid_o(valid_g), .result_o(result_g));
  interface_separate_add_mul_mac dut(
    .clk_i, .rst_ni, .valid_i, .mode_i, .a_i, .b_i, .c_i,
    .ready_o(ready_s), .valid_o(valid_s), .result_o(result_s));

  always #5 clk_i = ~clk_i;
  always @(posedge clk_i) if (rst_ni) cycles <= cycles + 1;

  function automatic [63:0] expected(input [1:0] mode, input [31:0] a,
                                      input [31:0] b, input [31:0] c);
    case (mode)
      2'b00: expected = {32'b0,a} + {32'b0,b};
      2'b01: expected = a * b;
      2'b10: expected = (a * b) + {32'b0,c};
      default: expected = '0;
    endcase
  endfunction

  task automatic transact(input [1:0] mode, input [31:0] a, input [31:0] b,
                          input [31:0] c);
    reg [63:0] want;
    begin
      while (!ready_g || !ready_s) @(negedge clk_i);
      mode_i = mode; a_i = a; b_i = b; c_i = c; valid_i = 1'b1;
      @(posedge clk_i);
      @(negedge clk_i);
      valid_i = 1'b0;
      want = expected(mode, a, b, c);
      if (!valid_g || (result_g !== want))
        $fatal(1, "generated one-cycle mismatch mode=%b got=%h want=%h", mode, result_g, want);
      if (mode == 2'b10) begin
        if (valid_s) $fatal(1, "separate MAC completed too early");
        @(negedge clk_i);
        if (valid_s) $fatal(1, "separate MAC completed after one cycle");
        @(negedge clk_i);
        if (!valid_s || result_s !== want)
          $fatal(1, "separate two-cycle MAC mismatch got=%h want=%h", result_s, want);
      end else if (!valid_s || result_s !== want) begin
        $fatal(1, "separate one-cycle mismatch mode=%b got=%h want=%h", mode, result_s, want);
      end
      checks = checks + 1;
    end
  endtask

  initial begin
    clk_i=0; rst_ni=0; valid_i=0; mode_i=0; a_i=0; b_i=0; c_i=0;
    checks=0; cycles=0;
    $dumpfile("interface_level_activity.vcd");
    $dumpvars(0, dut);
    repeat (3) @(negedge clk_i); rst_ni=1;
    transact(2'b00, 0, 0, 0);
    transact(2'b00, 32'hffffffff, 1, 0);
    transact(2'b01, 0, 32'hffffffff, 0);
    transact(2'b01, 32'hffffffff, 32'hffffffff, 0);
    transact(2'b10, 32'hffffffff, 2, 32'hffffffff);
    transact(2'b10, 32'h80000000, 2, 32'h7fffffff);
    for (integer i=0; i<64; i=i+1) begin
      transact(i[1:0] % 3, (i*32'h9e3779b9)+i,
              (i*32'h45d9f3b)+7, (i*32'h27d4eb2d)+32'h1234);
    end
    if (checks != 70) $fatal(1, "expected 70 checks got %0d", checks);
    $display("INTERFACE_LEVEL_TEST_PASS checks=%0d cycles=%0d generated_latency=1 separate_ADD_MUL=1 separate_MAC=2", checks, cycles);
    $finish;
  end
endmodule
/* verilator lint_on SYNCASYNCNET */
