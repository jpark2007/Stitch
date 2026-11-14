"""What one agent run is about: whose family, who started it, who is telling, and the form.

Tool factories close over a RunInfo so no tool ever takes a family id from the model.
"""

from __future__ import annotations

from dataclasses import dataclass

import schemas


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    family_id: str
    user_id: str
    author_id: str  # the teller's person id
    fields: schemas.SubmitMemoryFields
    photo_ids: tuple[str, ...] = ()

    @property
    def is_second_telling(self) -> bool:
        """True when this run adds a telling to an existing memory (fields.memory_id is set)."""
        return self.fields.memory_id is not None
