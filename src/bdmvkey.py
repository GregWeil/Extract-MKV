from dataclasses import dataclass
import hashlib
import os

@dataclass(frozen=True, eq=True)
class BdmvKey:
    name: str
    hash: str | None

    def identifier(self):
        return self.hash or self.name
    
@dataclass(frozen=True, eq=True)
class BdmvTitleKey:
    bdmv: BdmvKey
    title: str

def identify_bdmv_path(name: str, path: str) -> BdmvKey:
    unit_key_hash = None
    unit_key_path = os.path.join(path, "MAKEMKV", "AACS", "Unit_Key_RO.inf")
    if os.path.exists(unit_key_path):
        with open(unit_key_path, "rb") as unit_key_file:
            unit_key_hash = hashlib.file_digest(unit_key_file, "sha1").hexdigest()
    return BdmvKey(name, unit_key_hash)

def parse_bdmv_key(key: str) -> BdmvKey:
    keys = [s.strip() for s in key.split(":")]
    return BdmvKey(keys[0], keys[1] if len(keys) > 1 else None)