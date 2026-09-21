module tb_graph_fused_unit;
    logic source_select_i;
    logic add_mode_b_i, mul_mode_b_i, mac_mode_b_i;
    logic [31:0] a_a_i, b_a_i, a_b_i, b_b_i, c_b_i;
    logic [63:0] product_a_ref, result_b_ref;
    logic [63:0] product_a_gen, result_b_gen;
    integer i;
    integer errors = 0;
    logic [31:0] seed;

    graph_unit_a_mul ref_a (.*,
        .product_a_o(product_a_ref));
    graph_unit_b_add_mul_mac ref_b (.*,
        .result_b_o(result_b_ref));
    graph_fused_unit dut (.*,
        .product_a_o(product_a_gen), .result_b_o(result_b_gen));

    function automatic [31:0] next_value(input [31:0] value);
        next_value = (value ^ (value << 13)) ^ ((value ^ (value << 13)) >> 17);
        next_value = next_value ^ (next_value << 5);
    endfunction

    task automatic compare_value(input [63:0] expected, input [63:0] actual,
                                 input string label);
        if (actual !== expected) begin
            $display("FAIL %0s expected=%h actual=%h", label, expected, actual);
            errors = errors + 1;
        end
    endtask

    initial begin
        seed = 32'h1bad_f00d;
        for (i = 0; i < 70; i = i + 1) begin
            case (i)
                0: begin a_a_i=0; b_a_i=0; a_b_i=0; b_b_i=0; c_b_i=0; end
                1: begin a_a_i=32'hffff_ffff; b_a_i=1; a_b_i=32'hffff_ffff; b_b_i=1; c_b_i=0; end
                2: begin a_a_i=32'hffff_ffff; b_a_i=32'hffff_ffff; a_b_i=32'hffff_ffff; b_b_i=32'hffff_ffff; c_b_i=32'hffff_ffff; end
                3: begin a_a_i=32'h8000_0000; b_a_i=2; a_b_i=32'h8000_0000; b_b_i=2; c_b_i=32'hffff_ffff; end
                4: begin a_a_i=32'hffff_ffff; b_a_i=32'hffff_ffff; a_b_i=32'hffff_ffff; b_b_i=32'hffff_ffff; c_b_i=1; end
                5: begin a_a_i=1; b_a_i=32'hffff_ffff; a_b_i=1; b_b_i=32'hffff_ffff; c_b_i=32'hffff_ffff; end
                default: begin
                    seed = next_value(seed); a_a_i=seed;
                    seed = next_value(seed); b_a_i=seed;
                    seed = next_value(seed); a_b_i=seed;
                    seed = next_value(seed); b_b_i=seed;
                    seed = next_value(seed); c_b_i=seed;
                end
            endcase

            source_select_i = 1'b1; add_mode_b_i=0; mul_mode_b_i=1; mac_mode_b_i=0; #1;
            compare_value(a_a_i * b_a_i, product_a_ref, "A ref MUL");
            compare_value(product_a_ref, product_a_gen, "A generated MUL");
            compare_value(64'b0, result_b_gen, "A generated inactive B");

            source_select_i = 1'b0; add_mode_b_i=1; mul_mode_b_i=0; mac_mode_b_i=0; #1;
            compare_value({32'b0,a_b_i} + {32'b0,b_b_i}, result_b_ref, "B ref ADD");
            compare_value(result_b_ref, result_b_gen, "B generated ADD");
            compare_value(64'b0, product_a_gen, "B generated inactive A");

            add_mode_b_i=0; mul_mode_b_i=1; mac_mode_b_i=0; #1;
            compare_value(a_b_i * b_b_i, result_b_ref, "B ref MUL");
            compare_value(result_b_ref, result_b_gen, "B generated MUL");

            add_mode_b_i=0; mul_mode_b_i=0; mac_mode_b_i=1; #1;
            compare_value((a_b_i * b_b_i) + {32'b0,c_b_i}, result_b_ref, "B ref MAC");
            compare_value(result_b_ref, result_b_gen, "B generated MAC");
        end
        if (errors != 0) $fatal(1, "graph-to-RTL regression errors=%0d", errors);
        $display("GRAPH_TO_RTL_TEST_PASS cases=70 checks=%0d", 70*10);
        $finish;
    end
endmodule
