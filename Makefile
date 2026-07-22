PYTHON ?= python3
# SiliconCompiler lives in the project venv; the SC PPA flow uses this.
SC_PYTHON ?= .venv/bin/python
SIMULATOR ?= auto
SYNTHESIZER ?= auto
TARGET_LIBRARY ?=
CLOCK_PERIOD ?= 2.0
JOBS ?= 1
SEED ?= 1
TOOLCHAIN_LOCK ?= toolchain.lock.json

.PHONY: help doctor snapshot lock verify-lock test verilator-plan variant-plan variant-simulate variant-ppa simulate synthesize ppa ppa-sc demo demo-sc ibex-fetch sim-ibex ppa-ibex demo-ibex demo-all clean

help:
	@$(PYTHON) framework.py --help

doctor:
	$(PYTHON) toolchain.py doctor $(if $(TARGET_LIBRARY),--liberty $(TARGET_LIBRARY),)

snapshot:
	$(PYTHON) toolchain.py snapshot $(if $(TARGET_LIBRARY),--liberty $(TARGET_LIBRARY),)

lock:
	@test -n "$(TARGET_LIBRARY)" || (echo "TARGET_LIBRARY=/path/to/cells.lib is required"; exit 2)
	$(PYTHON) toolchain.py lock --liberty $(TARGET_LIBRARY) --lock-file $(TOOLCHAIN_LOCK)

verify-lock:
	$(PYTHON) toolchain.py verify --lock-file $(TOOLCHAIN_LOCK) \
		$(if $(TARGET_LIBRARY),--liberty $(TARGET_LIBRARY),)

test:
	$(PYTHON) -m unittest discover -s tests -v

verilator-plan:
	$(PYTHON) verilator_flow.py plan --output build/verilator_plan \
		--jobs $(JOBS) --seed $(SEED)

variant-plan:
	$(PYTHON) fusion_experiment.py plan --output build/fusion_variants \
		--jobs $(JOBS) --seed $(SEED)

variant-simulate:
	$(PYTHON) fusion_experiment.py run --output build/fusion_variants \
		--jobs $(JOBS) --seed $(SEED)

variant-ppa:
	@test -n "$(TARGET_LIBRARY)" || (echo "TARGET_LIBRARY=/path/to/cells.lib is required"; exit 2)
	$(PYTHON) toolchain.py verify --lock-file $(TOOLCHAIN_LOCK) \
		--liberty $(TARGET_LIBRARY)
	$(PYTHON) toolchain.py snapshot --liberty $(TARGET_LIBRARY) \
		--output build/toolchain.json --strict
	$(PYTHON) fusion_experiment.py evaluate --output build/fusion_evaluation \
		--target-library $(TARGET_LIBRARY) --clock-period $(CLOCK_PERIOD) \
		--toolchain-lock $(TOOLCHAIN_LOCK) --jobs $(JOBS) --seed $(SEED)

simulate:
	$(PYTHON) framework.py simulate --simulator $(SIMULATOR) \
		--jobs $(JOBS) --seed $(SEED) --clean --json

synthesize:
	$(PYTHON) framework.py synthesize --synthesizer $(SYNTHESIZER) \
		$(if $(TARGET_LIBRARY),--target-library $(TARGET_LIBRARY),) \
		--clock-period $(CLOCK_PERIOD) --clean --json

ppa:
	@test -n "$(TARGET_LIBRARY)" || (echo "TARGET_LIBRARY=/path/to/cells.lib is required"; exit 2)
	$(PYTHON) toolchain.py verify --lock-file $(TOOLCHAIN_LOCK) \
		--liberty $(TARGET_LIBRARY)
	$(PYTHON) toolchain.py snapshot --liberty $(TARGET_LIBRARY) \
		--output build/toolchain.json --strict
	$(PYTHON) framework.py all --simulator $(SIMULATOR) --synthesizer yosys \
		--target-library $(TARGET_LIBRARY) --clock-period $(CLOCK_PERIOD) \
		--toolchain-lock $(TOOLCHAIN_LOCK) --jobs $(JOBS) --seed $(SEED) --clean --json

demo:
	$(PYTHON) framework.py all --simulator $(SIMULATOR) --synthesizer $(SYNTHESIZER) \
		$(if $(TARGET_LIBRARY),--target-library $(TARGET_LIBRARY),) \
		--clock-period $(CLOCK_PERIOD) --jobs $(JOBS) --seed $(SEED) --clean --json

# End-to-end open-source demo: Verilator simulation + SiliconCompiler PPA on
# the Skywater130 PDK (Yosys -> OpenSTA). No Liberty/lock args needed; the SC
# flow supplies the sky130 standard-cell library and timing corners itself.
ppa-sc:
	$(SC_PYTHON) framework.py synthesize --adapter adapter.py \
		--synthesizer siliconcompiler --clean --json

demo-sc:
	$(SC_PYTHON) framework.py all --adapter adapter.py \
		--simulator $(SIMULATOR) --synthesizer siliconcompiler \
		--jobs $(JOBS) --seed $(SEED) --clean

# --- Real open-source functional unit: lowRISC Ibex ALU (Apache-2.0) ---------
# Fetches the pinned Ibex RTL if it is not already present, then runs the same
# simulate + SiliconCompiler PPA flow through adapters/ibex_adapter.py.
ibex-fetch:
	@test -f third_party/ibex/rtl/ibex_alu.sv || $(SC_PYTHON) designs/fetch_ibex.py

sim-ibex: ibex-fetch
	$(SC_PYTHON) framework.py simulate --adapter adapters/ibex_adapter.py \
		--jobs $(JOBS) --seed $(SEED) --clean --json

ppa-ibex: ibex-fetch
	$(SC_PYTHON) framework.py synthesize --adapter adapters/ibex_adapter.py \
		--synthesizer siliconcompiler --clean --json

demo-ibex: ibex-fetch
	$(SC_PYTHON) framework.py all --adapter adapters/ibex_adapter.py \
		--simulator $(SIMULATOR) --synthesizer siliconcompiler \
		--jobs $(JOBS) --seed $(SEED) --clean

# Run BOTH designs end to end: the local demo_alu and the open-source Ibex ALU.
demo-all: demo-sc demo-ibex

clean:
	rm -rf build
