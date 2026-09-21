module tb_egglog_spike;
  logic [7:0] x_i;
  logic [7:0] y_o;
  integer i;
  egglog_spike dut (.x_i(x_i), .y_o(y_o));
  initial begin
    for (i = 0; i < 256; i = i + 1) begin
      x_i = i[7:0]; #1;
      if (y_o !== x_i) $fatal(1, "egglog mismatch x=%h y=%h", x_i, y_o);
    end
    $display("EGGLOG_SPIKE_PASS checks=256");
    $finish;
  end
endmodule
