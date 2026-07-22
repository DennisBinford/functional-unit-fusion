`timescale 1ns/1ps

`ifndef DEMO_DUT_MODULE
`define DEMO_DUT_MODULE demo_alu
`endif

module tb_demo_alu;
  localparam int WIDTH = 32;
  localparam int SHAMT_WIDTH = (WIDTH > 1) ? $clog2(WIDTH) : 1;

  logic [WIDTH-1:0] a_i;
  logic [WIDTH-1:0] b_i;
  logic [2:0]       op_i;
  logic [WIDTH-1:0] result_o;
  integer checks;
  integer errors;
  integer iteration;
  integer unsigned stimulus_seed;
  logic [2:0] operation;
  logic [31:0] prng_state;
  logic [31:0] random_a, random_b;

  `DEMO_DUT_MODULE #(.WIDTH(WIDTH)) dut (
    .a_i(a_i),
    .b_i(b_i),
    .op_i(op_i),
    .result_o(result_o)
  );

  initial begin
    if ($test$plusargs("dump")) begin
      $dumpfile("activity.vcd");
      $dumpvars(0, dut);
    end
  end

  function automatic [WIDTH-1:0] reference_result(
    input logic [2:0]       op,
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

  task automatic check_case(
    input logic [2:0]       op,
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
      if (result_o !== expected) begin
        errors = errors + 1;
        $display(
          "TEST_ERROR op=%0d a=0x%08x b=0x%08x expected=0x%08x actual=0x%08x",
          op, a, b, expected, result_o
        );
      end
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

    // Directed corner cases.
    check_case(3'd0, 32'h0000_0000, 32'h0000_0000);
    check_case(3'd0, 32'hffff_ffff, 32'h0000_0001);
    check_case(3'd1, 32'h0000_0000, 32'h0000_0001);
    check_case(3'd5, 32'h8000_0000, 32'h0000_0001);
    check_case(3'd5, 32'h7fff_ffff, 32'hffff_ffff);
    check_case(3'd6, 32'h0000_0001, 32'h0000_001f);
    check_case(3'd7, 32'hdead_beef, 32'h1234_5678);

    // Deterministic randomized regression over every supported operation.
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
      $display("TEST_FAIL checks=%0d errors=%0d", checks, errors);
      $fatal(1, "demo_alu regression failed");
    end
    $display("TEST_PASS checks=%0d seed=%0d", checks, stimulus_seed);
    $finish;
  end

endmodule
