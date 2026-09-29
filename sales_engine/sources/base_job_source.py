from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseJobSource(ABC):
    @abstractmethod
    async def fetch_job(self, url: str) -> Dict[str, Any]:
        """
        Fetch and parse job details deterministically.
        Returns a dictionary with raw metadata ready for normalization.
        """
        pass
