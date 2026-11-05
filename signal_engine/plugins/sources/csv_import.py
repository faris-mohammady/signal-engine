"""Source plugin: accounts from a CSV the company already has.

Required columns: name, domain. Any other column is kept on the account as an
attribute (e.g. ats, ats_slug for the hiring_role plugin).

pack.yaml:
  discovery:
    - source: csv_import
      path: lists/targets.csv      # relative to the pack folder
"""

from __future__ import annotations

import csv

from signal_engine.core.models import AccountCandidate
from signal_engine.core.registry import source


@source("csv_import")
class CsvImport:
    def discover(self, pack, settings: dict, ctx) -> list[AccountCandidate]:
        rel = settings.get("path")
        if not rel:
            raise ValueError("csv_import needs a 'path' setting")
        path = pack.root / rel
        # Offline runs use the pack's bundled sample list, so editing your real
        # targets never breaks the demo or the tests.
        sample = pack.sample_dir / "targets.csv"
        if getattr(ctx, "offline", False) and sample.exists():
            path = sample
        if not path.exists():
            raise FileNotFoundError(f"csv_import: {path} not found")

        out: list[AccountCandidate] = []
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            missing = {"name", "domain"} - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"csv_import: {path.name} is missing columns {sorted(missing)}")
            for row in reader:
                name, domain = (row.get("name") or "").strip(), (row.get("domain") or "").strip()
                if not name or not domain or name.startswith("#"):
                    continue
                attrs = {k: v.strip() for k, v in row.items() if k not in ("name", "domain") and v and v.strip()}
                out.append(AccountCandidate(name=name, domain=domain, source="csv_import", attrs=attrs))
        return out
