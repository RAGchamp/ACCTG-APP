"""Tax rules are data, not code (plan §10.1): tax_rules\\<year>\\*.yaml."""

from functools import lru_cache

import yaml

import config


class TaxRulesMissing(ValueError):
    pass


@lru_cache(maxsize=16)
def load(year, name):
    path = config.TAX_RULES_DIR / str(year) / f"{name}.yaml"
    if not path.exists():
        raise TaxRulesMissing(f"No tax rules for {year}: {path} is missing. Copy the previous year's folder and "
                              "review every value against that year's forms.")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def version(year):
    return "+".join(f"{n}:{load(year, n).get('version')}" for n in ("federal", "sales_tax", "ct"))


def years():
    return sorted(p.name for p in config.TAX_RULES_DIR.iterdir() if p.is_dir())
