module procedural_style(input logic [7:0] a_i, b_i, output logic [7:0] y_o);
always_comb begin y_o = b_i + a_i; end
endmodule
