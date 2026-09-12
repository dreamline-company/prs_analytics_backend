"""Входы аналитики ремонта и их отпечаток.

Данные приходят асинхронно из разных добытчиков, поэтому общий вердикт и KPI
пересчитываются не «один раз», а при каждом изменении набора входов:
динамограмм до/после, закрытых замеров СПО (с их объёмом) и файлов ПОР/акта.
Отпечаток хранится рядом с вердиктом; совпал — LLM не дёргаем.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from apps.repairs.models.docs import RepairDoc
from apps.wells.models.dynamogram import Dynamogram
from apps.wells.models.spo import SPO


def inputs_fingerprint(
    *,
    before_id: int | None,
    after_id: int | None,
    spo_revisions: Mapping[int, int | None],
    por_file_id: int | None,
    act_file_id: int | None,
) -> str:
    payload = {
        "before": before_id,
        "after": after_id,
        "spo": sorted((int(spo_id), size) for spo_id, size in spo_revisions.items()),
        "por": por_file_id,
        "act": act_file_id,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(slots=True)
class RepairInputs:
    before: Dynamogram | None
    after: Dynamogram | None
    doc: RepairDoc | None
    # СПО скважины в окне ремонта по возрастанию времени снимка.
    spos: list[SPO]
    # Замеры, которые прибор уже не пишет: только их отдаём LLM.
    closed_spo_ids: frozenset[int]

    @property
    def primary_spo(self) -> SPO | None:
        return self.spos[0] if self.spos else None

    @property
    def closed_spos(self) -> list[SPO]:
        return [spo for spo in self.spos if spo.id in self.closed_spo_ids]

    @property
    def primary_spo_closed(self) -> bool:
        primary = self.primary_spo
        return primary is not None and primary.id in self.closed_spo_ids

    def fingerprint(self) -> str:
        return inputs_fingerprint(
            before_id=self.before.id if self.before else None,
            after_id=self.after.id if self.after else None,
            spo_revisions={spo.id: spo.raw_size for spo in self.closed_spos},
            por_file_id=self.doc.por_file_id if self.doc else None,
            act_file_id=self.doc.act_file_id if self.doc else None,
        )
