`timescale 1ns/1ps
module tb_generated_fused_add_mul_mac;
  /* verilator lint_off SYNCASYNCNET */
  logic clk_i, rst_ni, valid_i;
  logic [1:0] mode_i; logic [31:0] a_i, b_i, c_i;
  logic ready_f, valid_f; logic [63:0] result_f;
  logic ready_b, valid_b; logic [63:0] result_b;
  integer checks; integer cycles;
  generated_fused_add_mul_mac dut(.clk_i, .rst_ni, .valid_i, .mode_i,
                                    .a_i, .b_i, .c_i, .ready_o(ready_f),
                                    .valid_o(valid_f), .result_o(result_f));
  parallel_add_mul_mac_baseline baseline(.clk_i, .rst_ni, .valid_i, .mode_i,
                                         .a_i, .b_i, .c_i, .ready_o(ready_b),
                                         .valid_o(valid_b), .result_o(result_b));
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
    reg [63:0] want; integer accepted_cycle;
    begin
      while (!ready_f || !ready_b) @(negedge clk_i);
      mode_i=mode; a_i=a; b_i=b; c_i=c; valid_i=1;
      @(posedge clk_i); accepted_cycle=cycles;
      @(negedge clk_i); valid_i=0; want=expected(mode,a,b,c);
      if (!valid_f || !valid_b) $fatal(1,"latency violation mode=%b accepted=%0d now=%0d",mode,accepted_cycle,cycles);
      if (result_f !== want || result_b !== want || result_f !== result_b)
        $fatal(1,"mismatch mode=%b a=%h b=%h c=%h want=%h fused=%h base=%h",mode,a,b,c,want,result_f,result_b);
      checks=checks+1;
    end
  endtask

  initial begin
    clk_i=0; rst_ni=0; valid_i=0; checks=0; cycles=0;
    $dumpfile("generated_fused_activity.vcd"); $dumpvars(0, dut);
    repeat(3) @(negedge clk_i); rst_ni=1;
    transact(2'b00,0,0,0); transact(2'b00,32'hffffffff,1,0);
    transact(2'b01,0,32'hffffffff,0); transact(2'b01,32'hffffffff,32'hffffffff,0);
    transact(2'b10,32'hffffffff,2,32'hffffffff);
    transact(2'b10,32'h80000000,32'h2,32'h7fffffff);
    for (integer i=0;i<64;i=i+1) begin
      transact(i[1:0] % 3, (i*32'h9e3779b9)+i, (i*32'h45d9f3b)+7,
              (i*32'h27d4eb2d)+32'h1234);
    end
    if (checks != 70) $fatal(1,"expected 70 checks got %0d",checks);
    $display("GENERATED_FUSED_TEST_PASS checks=%0d cycles=%0d latency=1 ii=1",checks,cycles);
    $finish;
  end
endmodule
