from __future__ import annotations

from abc import ABC, abstractmethod

from clawbench_v2.models import AdapterRunContext, AdapterRunResult


class BaseAdapter(ABC):
    name = "base"

    @abstractmethod
    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        raise NotImplementedError

