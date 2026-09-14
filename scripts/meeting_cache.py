#!/usr/bin/env python3
"""Validate expensive meeting artifacts before a cached workflow reuses them."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VARIANTS = {"parallel", "radix2_original", "radix2_reused", "radix4_reused"}
BUNDLE_MANIFEST_NAME = "bundle_manifest.json"


def _sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read cache {}: {}".format(path, exc))


def build_manifest(paths, root=ROOT):
    """Build compact content-addressed records for a meeting evidence bundle."""
    root = Path(root).resolve()
    records = []
    seen_paths = set()
    for role, path in sorted(paths, key=lambda item: (item[0], str(item[1]))):
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError("manifest input is missing: {}".format(path))
        if path in seen_paths:
            continue
        seen_paths.add(path)
        try:
            recorded_path = str(path.relative_to(root))
            kind = "repository"
        except ValueError:
            recorded_path = str(path)
            kind = "external"
        records.append({"role": role, "kind": kind, "path": recorded_path,
                        "sha256": _sha256(path)})
    return {"schema": "professor-meeting-bundle/v1", "files": records}


def validate_bundle_manifest(path, root=ROOT):
    manifest = _load(path)
    if manifest.get("schema") != "professor-meeting-bundle/v1":
        raise ValueError("meeting bundle manifest schema is invalid")
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ValueError("meeting bundle manifest has no file records")
    seen = set()
    for record in records:
        file_path = Path(record.get("path", ""))
        if record.get("kind") == "repository":
            file_path = Path(root).resolve() / file_path
        elif record.get("kind") != "external":
            raise ValueError("meeting bundle manifest has an unknown path kind")
        if str(file_path) in seen:
            raise ValueError("meeting bundle manifest contains duplicate paths")
        seen.add(str(file_path))
        if not file_path.is_file() or record.get("sha256") != _sha256(file_path):
            raise ValueError("meeting bundle hash is stale or missing: {}".format(file_path))
    return manifest


def validate_fma_ppa(path, root=ROOT):
    data = _load(path)
    if data.get("schema_version") != 1:
        raise ValueError("PPA cache schema is not v1")
    method = data.get("method") or {}
    if set(method.get("expected_variants", [])) != EXPECTED_VARIANTS:
        raise ValueError("PPA cache expected-variant set is incomplete")
    if set(row.get("name") for row in data.get("variants", [])) != EXPECTED_VARIANTS:
        raise ValueError("PPA cache is missing one or more required variants")
    if method.get("pdk") != "FreePDK45" or method.get("clock_period_ns") != 10.0:
        raise ValueError("PPA cache conditions are not the required FreePDK45/10 ns run")
    for row in data["variants"]:
        simulation = row.get("evidence", {}).get("simulation", {})
        if row.get("status") != "pass" or simulation.get("status") != "pass":
            raise ValueError("PPA cache contains a failed variant")
        if simulation.get("checks") != 70 or row.get("activity_annotation_fraction") != 1.0:
            raise ValueError("PPA cache does not contain complete 70-case activity evidence")
    for name in EXPECTED_VARIANTS:
        spec_path = root / "rtl" / "fma_experiment" / {
            "parallel": "registered_parallel_mul_add.sv",
            "radix2_original": "shared_iterative_mul_add.sv",
            "radix2_reused": "shared_reused_adder_mul_add.sv",
            "radix4_reused": "radix4_reused_adder_mul_add.sv",
        }[name]
        recorded = (method.get("source_sha256") or {}).get(name)
        if recorded != _sha256(spec_path):
            raise ValueError("PPA cache source hash is stale for {}".format(name))
    lock = Path(method.get("toolchain_lock", ""))
    if not lock.is_file() or method.get("toolchain_lock_sha256") != _sha256(lock):
        raise ValueError("PPA cache toolchain lock is missing or stale")
    library = Path(method.get("library_path", ""))
    if not library.is_file() or method.get("library_sha256") != _sha256(library):
        raise ValueError("PPA cache library is missing or stale")
    return data


def validate_bundle(bundle, root=ROOT):
    bundle = Path(bundle)
    summary = _load(bundle / "summary.json")
    if summary.get("schema") != "professor-meeting/v2":
        raise ValueError("meeting summary is not the current v2 schema")
    validate_fma_ppa(root / "meeting-artifacts/fma-ppa/fma_ppa_results.json", root)
    fair = _load(bundle / "fair_baselines.json")
    if fair.get("schema") != "fair-baselines/v1":
        raise ValueError("fair-baseline cache schema is invalid")
    for path, recorded in (fair.get("source_sha256") or {}).items():
        if not Path(path).is_file() or recorded != _sha256(path):
            raise ValueError("fair-baseline source hash is stale: {}".format(path))
    guardrails = fair.get("guardrails") or {}
    for name in ("generated_triple_mode", "separate_system"):
        if (guardrails.get(name) or {}).get("operator_counts") != {"$add": 1, "$mul": 1}:
            raise ValueError("fair-baseline guardrail is missing one-MUL/one-ADD evidence for {}".format(name))
    manifest_path = bundle / "generated/generated_fused_add_mul_mac.sv.manifest.json"
    manifest = _load(manifest_path)
    if manifest.get("schema") != "fu-rtl-generator/v2" or manifest.get("structural_merge_executable") is not False:
        raise ValueError("generated manifest is missing current evidence status")
    for name, record in (manifest.get("inputs") or {}).items():
        path = Path(record.get("path", ""))
        if not path.is_file() or record.get("sha256") != _sha256(path):
            raise ValueError("generated input hash is stale: {}".format(name))
    rtl = bundle / "generated/generated_fused_add_mul_mac.sv"
    if manifest.get("generated_rtl_sha256") != _sha256(rtl):
        raise ValueError("generated RTL hash does not match manifest")
    bundle_manifest = bundle / BUNDLE_MANIFEST_NAME
    validate_bundle_manifest(bundle_manifest, root)
    required = [
        bundle / "roundtrip/roundtrip.json", bundle / "studies/capability.wide_to_narrow.match.json",
        bundle / "studies/capability.narrow_to_wide.match.json",
        bundle / "studies/horizontal.rtlil.match.json", bundle / "studies/vertical.rtlil.match.json",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("meeting cache is missing required evidence: {}".format(", ".join(missing)))
    return summary
