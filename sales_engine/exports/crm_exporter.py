import os
import json
from typing import List, Dict, Any

class CRMExporter:
    """Exports provider-neutral CRM payload for downstream CRM adapters."""

    def __init__(self):
        pass

    def export(
        self,
        output_path: str,
        payloads: List[Dict[str, Any]],
    ) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payloads, f, indent=2, ensure_ascii=False)
        return output_path
