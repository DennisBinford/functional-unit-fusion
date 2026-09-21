// Independent source A: a standalone unsigned 32x32-to-64 multiplier.
module graph_unit_a_mul (
    input  logic [31:0] a_a_i,
    input  logic [31:0] b_a_i,
    output logic [63:0] product_a_o
);
    logic [63:0] product_a;
    assign product_a = a_a_i * b_a_i;
    assign product_a_o = product_a;
endmodule
