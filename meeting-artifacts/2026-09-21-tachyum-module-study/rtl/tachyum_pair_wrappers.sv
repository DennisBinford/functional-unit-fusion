// Study-only focus wrappers.  Each top preserves one actual Tachyum child
// module and intentionally gives the two sides different wrapper/instance
// names so the module-level comparison tests name independence.

module tachyum_single_1_right_shifter (
    input  wire [73:0] data_i,
    input  wire [6:0]  shift_i,
    output wire [25:0] outside_o,
    output wire [73:0] shifted_o
);
    right_shifter_74_with_outside_bits lane_single_1 (
        .outside_data(outside_o),
        .right_shifted_data(shifted_o),
        .data_to_be_shifted_right(data_i),
        .right_shift_amount(shift_i)
    );
endmodule

module tachyum_single_2_right_shifter (
    input  wire [73:0] data_i,
    input  wire [6:0]  shift_i,
    output wire [25:0] outside_o,
    output wire [73:0] shifted_o
);
    right_shifter_74_with_outside_bits lane_single_2 (
        .outside_data(outside_o),
        .right_shifted_data(shifted_o),
        .data_to_be_shifted_right(data_i),
        .right_shift_amount(shift_i)
    );
endmodule

module tachyum_single_1_left_shifter (
    input  wire [75:0] data_i,
    input  wire [6:0]  shift_i,
    output wire [75:0] shifted_o
);
    left_shifter_76 lane_single_1 (
        .left_shifted_data(shifted_o),
        .data_to_be_shifted_left(data_i),
        .left_shift_amount(shift_i)
    );
endmodule

module tachyum_single_2_left_shifter (
    input  wire [75:0] data_i,
    input  wire [6:0]  shift_i,
    output wire [75:0] shifted_o
);
    left_shifter_76 lane_single_2 (
        .left_shifted_data(shifted_o),
        .data_to_be_shifted_left(data_i),
        .left_shift_amount(shift_i)
    );
endmodule

module tachyum_single_1_leading_zero_detector (
    input  wire [73:0] data_i,
    output wire [6:0]  count_o,
    output wire        all_zero_o,
    output wire [73:0] one_hot_o
);
    leading_zeros_detector_74_with_muxes lane_single_1 (
        .number_of_leading_zeros(count_o),
        .all_zeros(all_zero_o),
        .dout_one_hot(one_hot_o),
        .din(data_i)
    );
endmodule

module tachyum_single_2_leading_zero_detector (
    input  wire [73:0] data_i,
    output wire [6:0]  count_o,
    output wire        all_zero_o,
    output wire [73:0] one_hot_o
);
    leading_zeros_detector_74_with_muxes lane_single_2 (
        .number_of_leading_zeros(count_o),
        .all_zeros(all_zero_o),
        .dout_one_hot(one_hot_o),
        .din(data_i)
    );
endmodule
