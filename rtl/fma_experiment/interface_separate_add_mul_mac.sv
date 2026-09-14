`timescale 1ns/1ps

// Interface-level separate-system baseline: one multiplier and one adder.
// ADD/MUL use one registered operation. MAC is the natural two-stage
// multiply-then-add sequence through those same units, with no second adder.
module interface_separate_add_mul_mac (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        valid_i,
  input  logic [1:0]  mode_i,       // 00=ADD, 01=MUL, 10=MAC
  input  logic [31:0] a_i,
  input  logic [31:0] b_i,
  input  logic [31:0] c_i,
  output logic        ready_o,
  output logic        valid_o,
  output logic [63:0] result_o
);
  localparam logic [1:0] MODE_ADD = 2'b00;
  localparam logic [1:0] MODE_MUL = 2'b01;
  localparam logic [1:0] MODE_MAC = 2'b10;

  logic [31:0] a_q, b_q;
  logic [31:0] c_q;
  logic [63:0] product_q;
  logic        mac_mul_pending_q;
  logic        mac_add_pending_q;
  logic [31:0] mul_a;
  logic [31:0] mul_b;
  logic [63:0] mul_result;
  logic [63:0] add_a;
  logic [63:0] add_b;
  logic [63:0] add_result;

  assign ready_o = rst_ni &&
                   !mac_mul_pending_q && !mac_add_pending_q;

  // One shared combinational multiplier and one shared combinational adder.
  // Their operands are selected from the input transaction or MAC state.
  always_comb begin
    mul_a = a_i;
    mul_b = b_i;
    add_a = '0;
    add_b = '0;
    if (mac_mul_pending_q) begin
      mul_a = a_q;
      mul_b = b_q;
    end
    if (mac_add_pending_q) begin
      add_a = product_q;
      add_b = {32'b0, c_q};
    end else if (valid_i && ready_o && mode_i == MODE_ADD) begin
      add_a = {32'b0, a_i};
      add_b = {32'b0, b_i};
    end
  end

  assign mul_result = mul_a * mul_b;
  assign add_result = add_a + add_b;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      a_q                <= '0;
      b_q                <= '0;
      c_q                <= '0;
      product_q          <= '0;
      mac_mul_pending_q  <= 1'b0;
      mac_add_pending_q  <= 1'b0;
      valid_o            <= 1'b0;
      result_o           <= '0;
    end else begin
      valid_o <= 1'b0;

      if (mac_add_pending_q) begin
        // The shared adder completes the second MAC stage.
        result_o          <= add_result;
        valid_o           <= 1'b1;
        mac_add_pending_q <= 1'b0;
      end else if (mac_mul_pending_q) begin
        // The shared multiplier completes the first MAC stage.
        product_q         <= mul_result;
        mac_mul_pending_q <= 1'b0;
        mac_add_pending_q <= 1'b1;
      end else if (valid_i && ready_o) begin
        a_q <= a_i;
        b_q <= b_i;
        c_q <= c_i;
        if (mode_i == MODE_MAC) begin
          mac_mul_pending_q <= 1'b1;
        end else if (mode_i == MODE_ADD) begin
          result_o <= add_result;
          valid_o <= 1'b1;
        end else if (mode_i == MODE_MUL) begin
          result_o <= mul_result;
          valid_o <= 1'b1;
        end else begin
          result_o <= '0;
          valid_o <= 1'b1;
        end
      end
    end
  end
endmodule
