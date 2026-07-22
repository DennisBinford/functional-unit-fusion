`timescale 1ns/1ps

// Common regression for all fusion baselines. This is a pre-tool functional
// gate; formal equivalence and independent golden vectors remain stronger gates.
module tb_demo_variants;
  localparam int WIDTH = 32;
  localparam int SHAMT_WIDTH = (WIDTH > 1) ? $clog2(WIDTH) : 1;
  localparam int VARIANTS = 6;

  logic [WIDTH-1:0] a_i, b_i;
  logic [2:0] op_i;
  logic [WIDTH-1:0] fused_auto_o, separate_locked_o;
  logic [WIDTH-1:0] separate_isolated_o, separate_flat_o, manual_fused_o;
  logic [WIDTH-1:0] dedicated_add_o, dedicated_sub_o, dedicated_and_o;
  logic [WIDTH-1:0] dedicated_or_o, dedicated_xor_o, dedicated_slt_o, dedicated_sll_o;
  logic [WIDTH-1:0] dedicated_selected_o;
  integer checks, errors, iteration;
  integer unsigned stimulus_seed;
  logic [2:0] operation;
  logic [31:0] prng_state;
  logic [31:0] random_a, random_b;

  demo_alu #(.WIDTH(WIDTH)) fused_auto (
    .a_i(a_i), .b_i(b_i), .op_i(op_i), .result_o(fused_auto_o)
  );
  demo_alu_separate_locked #(.WIDTH(WIDTH)) separate_locked (
    .a_i(a_i), .b_i(b_i), .op_i(op_i), .result_o(separate_locked_o)
  );
  demo_alu_separate_locked_isolated #(.WIDTH(WIDTH)) separate_isolated (
    .a_i(a_i), .b_i(b_i), .op_i(op_i), .result_o(separate_isolated_o)
  );
  demo_alu_separate_flat_auto #(.WIDTH(WIDTH)) separate_flat (
    .a_i(a_i), .b_i(b_i), .op_i(op_i), .result_o(separate_flat_o)
  );
  demo_alu_manual_fused #(.WIDTH(WIDTH)) manual_fused (
    .a_i(a_i), .b_i(b_i), .op_i(op_i), .result_o(manual_fused_o)
  );
  demo_alu_dedicated_parallel #(.WIDTH(WIDTH)) dedicated (
    .a_i(a_i), .b_i(b_i),
    .add_o(dedicated_add_o), .sub_o(dedicated_sub_o),
    .and_o(dedicated_and_o), .or_o(dedicated_or_o),
    .xor_o(dedicated_xor_o), .slt_o(dedicated_slt_o), .sll_o(dedicated_sll_o)
  );

  always_comb begin
    dedicated_selected_o = '0;
    case (op_i)
      3'd0: dedicated_selected_o = dedicated_add_o;
      3'd1: dedicated_selected_o = dedicated_sub_o;
      3'd2: dedicated_selected_o = dedicated_and_o;
      3'd3: dedicated_selected_o = dedicated_or_o;
      3'd4: dedicated_selected_o = dedicated_xor_o;
      3'd5: dedicated_selected_o = dedicated_slt_o;
      3'd6: dedicated_selected_o = dedicated_sll_o;
      default: dedicated_selected_o = '0;
    endcase
  end

  initial begin
    if ($test$plusargs("dump")) begin
      $dumpfile("activity_variants.vcd");
      $dumpvars(0, tb_demo_variants);
    end
  end

  function automatic [WIDTH-1:0] reference_result(
    input logic [2:0] op,
    input logic [WIDTH-1:0] a,
    input logic [WIDTH-1:0] b
  );
    begin
      case (op)
        3'd0: reference_result = a + b;
        3'd1: reference_result = a - b;
        3'd2: reference_result = a & b;
        3'd3: reference_result = a | b;
        3'd4: reference_result = a ^ b;
        3'd5: reference_result = {{(WIDTH-1){1'b0}}, ($signed(a) < $signed(b))};
        3'd6: reference_result = (WIDTH > 1) ? (a << b[SHAMT_WIDTH-1:0]) : a;
        default: reference_result = '0;
      endcase
    end
  endfunction

  function automatic [31:0] xorshift32(input logic [31:0] value);
    logic [31:0] next_value;
    begin
      next_value = value;
      next_value = next_value ^ (next_value << 13);
      next_value = next_value ^ (next_value >> 17);
      next_value = next_value ^ (next_value << 5);
      xorshift32 = next_value;
    end
  endfunction

  task automatic compare_result(
    input string name,
    input logic [WIDTH-1:0] actual,
    input logic [WIDTH-1:0] expected
  );
    begin
      if (actual !== expected) begin
        errors = errors + 1;
        $display(
          "VARIANT_ERROR name=%s op=%0d a=0x%08x b=0x%08x expected=0x%08x actual=0x%08x",
          name, op_i, a_i, b_i, expected, actual
        );
      end
    end
  endtask

  task automatic check_case(
    input logic [2:0] op,
    input logic [WIDTH-1:0] a,
    input logic [WIDTH-1:0] b
  );
    logic [WIDTH-1:0] expected;
    begin
      op_i = op;
      a_i = a;
      b_i = b;
      expected = reference_result(op, a, b);
      #1;
      checks = checks + 1;
      compare_result("fused_auto", fused_auto_o, expected);
      compare_result("separate_locked", separate_locked_o, expected);
      compare_result("separate_isolated", separate_isolated_o, expected);
      compare_result("separate_flat_auto", separate_flat_o, expected);
      compare_result("fused_manual", manual_fused_o, expected);
      compare_result("dedicated_parallel", dedicated_selected_o, expected);
    end
  endtask

  initial begin
    checks = 0;
    errors = 0;
    a_i = '0;
    b_i = '0;
    op_i = '0;
    if (!$value$plusargs("fu_seed=%d", stimulus_seed)) begin
      stimulus_seed = 1;
    end
    prng_state = (stimulus_seed == 0) ? 32'h6d2b_79f5 : stimulus_seed;

    check_case(3'd0, 32'h0000_0000, 32'h0000_0000);
    check_case(3'd0, 32'hffff_ffff, 32'h0000_0001);
    check_case(3'd1, 32'h0000_0000, 32'h0000_0001);
    check_case(3'd5, 32'h8000_0000, 32'h0000_0001);
    check_case(3'd5, 32'h7fff_ffff, 32'hffff_ffff);
    check_case(3'd6, 32'h0000_0001, 32'h0000_001f);
    check_case(3'd7, 32'hdead_beef, 32'h1234_5678);

    for (iteration = 0; iteration < 250; iteration = iteration + 1) begin
      for (operation = 3'd0; operation < 3'd7; operation = operation + 3'd1) begin
        prng_state = xorshift32(prng_state);
        random_a = prng_state;
        prng_state = xorshift32(prng_state);
        random_b = prng_state;
        check_case(operation, random_a, random_b);
      end
    end

    if (errors != 0) begin
      $display("VARIANT_TEST_FAIL checks=%0d variants=%0d errors=%0d", checks, VARIANTS, errors);
      $fatal(1, "demo ALU variant regression failed");
    end
    $display(
      "VARIANT_TEST_PASS checks=%0d variants=%0d seed=%0d",
      checks, VARIANTS, stimulus_seed
    );
    $finish;
  end
endmodule
