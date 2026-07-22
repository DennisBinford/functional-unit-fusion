`timescale 1ns/1ps

// Independent self-checking testbench for the isolated Ibex ALU
// (ibex_alu_wrapper). The reference model below is coded from the RISC-V base
// integer semantics, separately from the Ibex RTL, so a match is real evidence
// of functional correctness for the base RV32I operations.
module tb_ibex_alu
  import ibex_pkg::*;
();
  localparam int WIDTH = 32;

  logic [6:0]  operator_i;
  logic [31:0] operand_a_i;
  logic [31:0] operand_b_i;
  logic [31:0] result_o;

  integer checks;
  integer errors;
  integer iteration;
  integer op_index;
  integer unsigned stimulus_seed;
  logic [31:0] prng_state;
  logic [31:0] random_a, random_b;

  // Base RV32I operations exercised under RV32BNone.
  localparam int NUM_OPS = 14;
  alu_op_e ops [NUM_OPS];

  ibex_alu_wrapper dut (
    .operator_i (operator_i),
    .operand_a_i(operand_a_i),
    .operand_b_i(operand_b_i),
    .result_o   (result_o)
  );

  initial begin
    if ($test$plusargs("dump")) begin
      $dumpfile("activity.vcd");
      $dumpvars(0, dut);
    end
  end

  // Reference model coded independently from the Ibex RTL.
  function automatic [31:0] reference_result(
    input alu_op_e        op,
    input logic [31:0]    a,
    input logic [31:0]    b
  );
    logic [4:0] shamt;
    begin
      shamt = b[4:0];
      unique case (op)
        ALU_ADD: reference_result = a + b;
        ALU_SUB: reference_result = a - b;
        ALU_XOR: reference_result = a ^ b;
        ALU_OR : reference_result = a | b;
        ALU_AND: reference_result = a & b;
        ALU_SLL: reference_result = a << shamt;
        ALU_SRL: reference_result = a >> shamt;
        ALU_SRA: reference_result = $signed(a) >>> shamt;
        ALU_LT : reference_result = {31'b0, ($signed(a)  < $signed(b))};
        ALU_LTU: reference_result = {31'b0, (a           < b)};
        ALU_GE : reference_result = {31'b0, ($signed(a) >= $signed(b))};
        ALU_GEU: reference_result = {31'b0, (a          >= b)};
        ALU_EQ : reference_result = {31'b0, (a == b)};
        ALU_NE : reference_result = {31'b0, (a != b)};
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
    input alu_op_e     op,
    input logic [31:0] a,
    input logic [31:0] b
  );
    logic [31:0] expected;
    begin
      operator_i  = op;
      operand_a_i = a;
      operand_b_i = b;
      expected = reference_result(op, a, b);
      #1;
      checks = checks + 1;
      if (result_o !== expected) begin
        errors = errors + 1;
        $display("TEST_ERROR op=%0d a=0x%08x b=0x%08x expected=0x%08x actual=0x%08x",
                 op, a, b, expected, result_o);
      end
    end
  endtask

  initial begin
    ops[0]  = ALU_ADD; ops[1]  = ALU_SUB; ops[2]  = ALU_XOR; ops[3]  = ALU_OR;
    ops[4]  = ALU_AND; ops[5]  = ALU_SLL; ops[6]  = ALU_SRL; ops[7]  = ALU_SRA;
    ops[8]  = ALU_LT;  ops[9]  = ALU_LTU; ops[10] = ALU_GE;  ops[11] = ALU_GEU;
    ops[12] = ALU_EQ;  ops[13] = ALU_NE;

    checks = 0;
    errors = 0;
    operator_i = '0;
    operand_a_i = '0;
    operand_b_i = '0;
    if (!$value$plusargs("fu_seed=%d", stimulus_seed)) begin
      stimulus_seed = 1;
    end
    prng_state = (stimulus_seed == 0) ? 32'h6d2b_79f5 : stimulus_seed;

    // Directed corner cases.
    check_case(ALU_ADD, 32'h0000_0000, 32'h0000_0000);
    check_case(ALU_ADD, 32'hffff_ffff, 32'h0000_0001);
    check_case(ALU_SUB, 32'h0000_0000, 32'h0000_0001);
    check_case(ALU_SLL, 32'h0000_0001, 32'h0000_001f);
    check_case(ALU_SRA, 32'h8000_0000, 32'h0000_0001);
    check_case(ALU_LT,  32'h8000_0000, 32'h0000_0001);
    check_case(ALU_LTU, 32'h8000_0000, 32'h0000_0001);
    check_case(ALU_EQ,  32'h1234_5678, 32'h1234_5678);

    // Deterministic randomized regression over every base operation.
    for (iteration = 0; iteration < 250; iteration = iteration + 1) begin
      for (op_index = 0; op_index < NUM_OPS; op_index = op_index + 1) begin
        prng_state = xorshift32(prng_state);
        random_a = prng_state;
        prng_state = xorshift32(prng_state);
        random_b = prng_state;
        check_case(ops[op_index], random_a, random_b);
      end
    end

    if (errors != 0) begin
      $display("TEST_FAIL checks=%0d errors=%0d", checks, errors);
      $fatal(1, "ibex_alu regression failed");
    end
    $display("TEST_PASS checks=%0d seed=%0d", checks, stimulus_seed);
    $finish;
  end

endmodule
