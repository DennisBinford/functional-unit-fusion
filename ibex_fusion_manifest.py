#!/usr/bin/env python3
"""Portable validation for the post-checkpoint Ibex fusion evidence bundle."""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict


class IbexManifestError(ValueError):
    """Raised when an Ibex fusion manifest is missing or stale."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_manifest(manifest_path: Path, repository_root: Path) -> Dict[str, Any]:
    """Validate every repository-relative and external evidence record.

    The manifest intentionally does not hash itself, avoiding a self-reference
    cycle.  Repository files are resolved only from ``repository_root``;
    external records must use an absolute path and are reported separately.
    """
    manifest_path = Path(manifest_path)
    repository_root = Path(repository_root).resolve()
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise IbexManifestError("cannot read manifest {}: {}".format(manifest_path, exc))
    schema = manifest.get("schema")
    if schema not in ("fu-ibex-fusion-manifest/v1", "fu-ibex-module-preserving-manifest/v1"):
        raise IbexManifestError("unexpected Ibex manifest schema")
    if schema == "fu-ibex-fusion-manifest/v1":
        if manifest.get("template_backed") is not False or manifest.get("graph_to_rtl_status") != "executable":
            raise IbexManifestError("manifest does not describe a validated executable realization")
    else:
        if manifest.get("classification") != "contract-guided module-preserving fusion":
            raise IbexManifestError("module manifest has the wrong claim classification")
    records = manifest.get("evidence")
    if not isinstance(records, dict) or not records:
        raise IbexManifestError("manifest has no evidence records")
    checked = []
    external = []
    all_records = dict(records)
    external_records = manifest.get("external_dependencies") or {}
    if not isinstance(external_records, dict):
        raise IbexManifestError("external_dependencies is malformed")
    all_records.update({"external:" + name: record for name, record in external_records.items()})
    for name, record in sorted(all_records.items()):
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise IbexManifestError("{} has an invalid evidence record".format(name))
        path = Path(record["path"])
        if record.get("external"):
            if not path.is_absolute():
                raise IbexManifestError("external record {} is not absolute".format(name))
            external.append(name)
        else:
            if path.is_absolute() or ".." in path.parts:
                raise IbexManifestError("repository record {} escapes repository root".format(name))
            path = repository_root / path
            checked.append(name)
        if not path.is_file():
            raise IbexManifestError("evidence {} is missing: {}".format(name, path))
        expected = record.get("sha256")
        if not isinstance(expected, str) or _sha256(path) != expected:
            raise IbexManifestError("evidence {} has a hash mismatch".format(name))
    return {"valid": True, "checked": checked, "external": external}


__all__ = ["IbexManifestError", "validate_manifest"]
