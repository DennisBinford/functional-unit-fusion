`timescale 1ns/1ps

module tb_core_semantics;
  logic [1:0] mode_i;
  logic [31:0] a_i, b_i, c_i;
  logic [63:0] add_result, mul_result, separate_mac_result, fused_result;
  logic [63:0] add_a_i, add_b_i;
  integer checks;

  core_add_u64 add_dut(.a_i(add_a_i), .b_i(add_b_i), .result_o(add_result));
  core_mul_u32 mul_dut(.a_i, .b_i, .result_o(mul_result));
  core_fused_add_mul_mac_u32 fused_dut(.mode_i, .a_i, .b_i, .c_i, .result_o(fused_result));

  task automatic check_case(input [1:0] mode, input [31:0] a, input [31:0] b,
                            input [31:0] c);
    reg [63:0] aa, bb, cc, want_add, want_mul, want_mac;
    begin
      mode_i = mode; a_i = a; b_i = b; c_i = c;
      add_a_i = {32'b0, a}; add_b_i = {32'b0, b}; #1;
      aa = {32'b0, a}; bb = {32'b0, b}; cc = {32'b0, c};
      want_add = aa + bb;
      want_mul = aa * bb;
      want_mac = want_mul + cc;
      if (add_result !== want_add || mul_result !== want_mul)
        $fatal(1, "operator mismatch a=%h b=%h add=%h/%h mul=%h/%h",
               a, b, add_result, want_add, mul_result, want_mul);
      // Explicit separate-system composition: MUL result feeds the same
      // 64-bit adder with zero-extended c.
      add_a_i = mul_result; add_b_i = cc; #1;
      separate_mac_result = add_result;
      if (separate_mac_result !== want_mac)
        $fatal(1, "separate MUL->ADD mismatch a=%h b=%h c=%h got=%h want=%h",
               a, b, c, separate_mac_result, want_mac);
      if (fused_result !== (mode == 2'b00 ? want_add :
                            mode == 2'b01 ? want_mul : want_mac))
        $fatal(1, "fused mismatch mode=%b a=%h b=%h c=%h got=%h",
               mode, a, b, c, fused_result);
      checks = checks + 1;
    end
  endtask

  initial begin
    checks = 0; mode_i = 0; a_i = 0; b_i = 0; c_i = 0;
    add_a_i = 0; add_b_i = 0;
    check_case(2'b00, 32'hffffffff, 32'h00000001, 0);
    check_case(2'b00, 32'hffffffff, 32'hffffffff, 0);
    check_case(2'b01, 32'hffffffff, 32'hffffffff, 0);
    check_case(2'b10, 32'hffffffff, 32'hffffffff, 32'hffffffff);
    check_case(2'b10, 32'h80000000, 32'h00000002, 32'h7fffffff);
    check_case(2'b00, 0, 32'hffffffff, 0);
    for (integer i = 0; i < 64; i = i + 1)
      check_case(i[1:0] % 3, (i * 32'h9e3779b9) + i,
                 (i * 32'h45d9f3b) + 7, (i * 32'h27d4eb2d) + 32'h1234);
    if (checks != 70) $fatal(1, "expected 70 checks got %0d", checks);
    $display("CORE_SEMANTICS_TEST_PASS checks=%0d composition_checks=%0d", checks, checks);
    $finish;
  end
endmodule
