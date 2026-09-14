import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from meeting_cache import build_manifest, validate_bundle_manifest, validate_fma_ppa


ROOT = Path(__file__).resolve().parents[1]
PPA = ROOT / "meeting-artifacts/fma-ppa/fma_ppa_results.json"


class MeetingCacheTest(unittest.TestCase):
    def _copy(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / "ppa.json"
        path.write_text(PPA.read_text())
        return directory, path, json.loads(path.read_text())

    def test_cache_rejects_missing_variant(self):
        directory, path, data = self._copy()
        self.addCleanup(lambda: directory.rmdir())
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        data["variants"] = data["variants"][:-1]
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            validate_fma_ppa(path, ROOT)

    def test_cache_rejects_altered_source_hash(self):
        directory, path, data = self._copy()
        self.addCleanup(lambda: directory.rmdir())
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        data["method"]["source_sha256"]["parallel"] = "altered"
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            validate_fma_ppa(path, ROOT)

    def _manifest_fixture(self):
        directory = Path(tempfile.mkdtemp())
        study = directory / "vertical.rtlil.match.json"
        graph_source = directory / "graph_match.py"
        sc_source = directory / "sc_flow.py"
        sdc = directory / "core_10ns.sdc"
        study.write_text('{"matches": 7}\n')
        graph_source.write_text("def match(): return True\n")
        sc_source.write_text("def synthesize(): return True\n")
        sdc.write_text("create_clock -period 10 [get_ports clk_i]\n")
        manifest = directory / "bundle_manifest.json"
        manifest.write_text(json.dumps(build_manifest([
            ("study", study), ("graph_matching_source", graph_source),
            ("sc_flow_source", sc_source), ("sdc", sdc)])))
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        return directory, study, graph_source, sc_source, sdc, manifest

    def test_bundle_cache_rejects_mutated_study_json(self):
        _, study, _, _, _, manifest = self._manifest_fixture()
        validate_bundle_manifest(manifest)
        study.write_text('{"matches": 8}\n')
        with self.assertRaises(ValueError):
            validate_bundle_manifest(manifest)

    def test_bundle_cache_rejects_mutated_graph_matching_source(self):
        _, _, graph_source, _, _, manifest = self._manifest_fixture()
        validate_bundle_manifest(manifest)
        graph_source.write_text("def match(): return False\n")
        with self.assertRaises(ValueError):
            validate_bundle_manifest(manifest)

    def test_bundle_cache_rejects_mutated_sc_flow_source(self):
        _, _, _, sc_source, _, manifest = self._manifest_fixture()
        validate_bundle_manifest(manifest)
        sc_source.write_text("def synthesize(): return False\n")
        with self.assertRaises(ValueError):
            validate_bundle_manifest(manifest)

    def test_bundle_cache_rejects_mutated_sdc(self):
        _, _, _, _, sdc, manifest = self._manifest_fixture()
        validate_bundle_manifest(manifest)
        sdc.write_text("create_clock -period 9 [get_ports clk_i]\n")
        with self.assertRaises(ValueError):
            validate_bundle_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
