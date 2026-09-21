`timescale 1ns/1ps
module tb_module_preserving;
  import ibex_pkg::*;
  logic [6:0] operator_i;
  logic [31:0] alu_operand_a_i, alu_operand_b_i, client_operand_a_i, client_operand_b_i;
  logic client_select_i;
  logic [31:0] canonical_result, separate_result, fused_result, dedicated_result;
  integer checks, errors, i, j;
  logic [31:0] state;
  alu_op_e ops [14];

  ibex_alu_wrapper canonical(.operator_i(operator_i), .operand_a_i(alu_operand_a_i),
                             .operand_b_i(alu_operand_b_i), .result_o(canonical_result));
  ibex_alu_add_client_separate separate(
    .operator_i(operator_i), .alu_operand_a_i(alu_operand_a_i), .alu_operand_b_i(alu_operand_b_i),
    .client_operand_a_i(client_operand_a_i), .client_operand_b_i(client_operand_b_i),
    .client_select_i(client_select_i), .result_o(separate_result));
  ibex_alu_add_client_fused fused(
    .operator_i(operator_i), .alu_operand_a_i(alu_operand_a_i), .alu_operand_b_i(alu_operand_b_i),
    .client_operand_a_i(client_operand_a_i), .client_operand_b_i(client_operand_b_i),
    .client_select_i(client_select_i), .result_o(fused_result));
  ibex_independent_add_client dedicated(.operand_a_i(client_operand_a_i),
                                        .operand_b_i(client_operand_b_i), .result_o(dedicated_result));

  function automatic [31:0] model(input alu_op_e op, input [31:0] a, input [31:0] b);
    begin
      case (op)
        ALU_ADD: model=a+b; ALU_SUB: model=a-b; ALU_XOR: model=a^b; ALU_OR: model=a|b; ALU_AND: model=a&b;
        ALU_SLL: model=a<<b[4:0]; ALU_SRL: model=a>>b[4:0]; ALU_SRA: model=$signed(a)>>>b[4:0];
        ALU_LT: model={31'b0,($signed(a)<$signed(b))}; ALU_LTU: model={31'b0,(a<b)};
        ALU_GE: model={31'b0,($signed(a)>=$signed(b))}; ALU_GEU: model={31'b0,(a>=b)};
        ALU_EQ: model={31'b0,(a==b)}; ALU_NE: model={31'b0,(a!=b)}; default: model='0;
      endcase
    end
  endfunction
  function automatic [31:0] rnd(input [31:0] x);
    begin rnd=x^(x<<13); rnd=rnd^(rnd>>17); rnd=rnd^(rnd<<5); end
  endfunction
  task automatic check_alu(input alu_op_e op, input [31:0] a, input [31:0] b);
    begin
      operator_i=op; alu_operand_a_i=a; alu_operand_b_i=b;
      client_operand_a_i=32'h13579bdf; client_operand_b_i=32'h2468ace0; client_select_i=1'b0; #1;
      checks=checks+1; if (fused_result !== canonical_result || fused_result !== model(op,a,b)) begin errors=errors+1; $display("ALU_FAIL"); end
      checks=checks+1; if (separate_result !== fused_result) errors=errors+1;
      client_select_i=1'b1; #1;
      checks=checks+1; if (fused_result !== dedicated_result || fused_result !== separate_result) errors=errors+1;
    end
  endtask
  task automatic check_client(input [31:0] a, input [31:0] b);
    begin
      operator_i=ALU_ADD; alu_operand_a_i=32'hdeadbeef; alu_operand_b_i=32'h01020304;
      client_operand_a_i=a; client_operand_b_i=b; client_select_i=1'b1; #1;
      checks=checks+1; if (fused_result !== (a+b)) errors=errors+1;
      checks=checks+1; if (separate_result !== fused_result || fused_result !== dedicated_result) errors=errors+1;
      client_select_i=1'b0; #1; checks=checks+1; if (fused_result !== canonical_result) errors=errors+1;
    end
  endtask
  initial begin
    ops[0]=ALU_ADD; ops[1]=ALU_SUB; ops[2]=ALU_XOR; ops[3]=ALU_OR; ops[4]=ALU_AND; ops[5]=ALU_SLL; ops[6]=ALU_SRL; ops[7]=ALU_SRA;
    ops[8]=ALU_LT; ops[9]=ALU_LTU; ops[10]=ALU_GE; ops[11]=ALU_GEU; ops[12]=ALU_EQ; ops[13]=ALU_NE;
    checks=0; errors=0; state=32'h1;
    for (j=0;j<14;j=j+1) begin
      check_alu(ops[j],32'h0,32'h0); check_alu(ops[j],32'hffffffff,32'h1); check_alu(ops[j],32'h80000000,32'h7fffffff); check_alu(ops[j],32'h7fffffff,32'h80000000);
      check_alu(ops[j],32'hffffffff,32'hffffffff); check_alu(ops[j],32'h1,32'hffffffff); check_alu(ops[j],32'h80000000,32'h1); check_alu(ops[j],32'h12345678,32'h12345678);
      for (i=0;i<256;i=i+1) begin state=rnd(state); check_alu(ops[j],state,rnd(state)); end
    end
    check_client(0,0); check_client(32'hffffffff,1); check_client(32'hffffffff,32'hffffffff); check_client(32'h80000000,32'h80000000); check_client(32'h7fffffff,32'h1);
    for (i=0;i<256;i=i+1) begin state=rnd(state); check_client(state,rnd(state)); end
    if (errors != 0) $fatal(1,"module preserving regression failed errors=%0d",errors);
    $display("IBEX_MODULE_PRESERVING_PASS checks=%0d alu_operations=14 client_vectors=261",checks); $finish;
  end
endmodule
