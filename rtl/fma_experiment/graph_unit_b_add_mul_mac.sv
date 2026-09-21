// Independent source B: ADD, MUL, and MAC modes with its own datapath.
module graph_unit_b_add_mul_mac (
    input  logic        add_mode_b_i,
    input  logic        mul_mode_b_i,
    input  logic        mac_mode_b_i,
    input  logic [31:0] a_b_i,
    input  logic [31:0] b_b_i,
    input  logic [31:0] c_b_i,
    output logic [63:0] result_b_o
);
    logic [63:0] product_b;
    logic [63:0] add_lhs_b;
    logic [63:0] add_rhs_b;
    logic [63:0] result_b;

    assign product_b = a_b_i * b_b_i;
    assign add_lhs_b = add_mode_b_i ? {32'b0, a_b_i} : product_b;
    assign add_rhs_b = add_mode_b_i ? {32'b0, b_b_i} :
                       (mac_mode_b_i ? {32'b0, c_b_i} : 64'b0);
    assign result_b = mul_mode_b_i ? product_b : add_lhs_b + add_rhs_b;
    assign result_b_o = result_b;
endmodule
