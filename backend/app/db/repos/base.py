"""Generic repository base. Repos are the only DB access surface for services."""

from __future__ import annotations

from typing import Generic, TypeVar

from beanie import Document, PydanticObjectId

TDoc = TypeVar("TDoc", bound=Document)


class BaseRepo(Generic[TDoc]):
    def __init__(self, model: type[TDoc]) -> None:
        self._model = model

    async def insert(self, doc: TDoc) -> TDoc:
        return await doc.insert()

    async def get(self, doc_id: PydanticObjectId) -> TDoc | None:
        return await self._model.get(doc_id)

    async def all(self) -> list[TDoc]:
        return await self._model.find_all().to_list()

    async def count(self) -> int:
        return await self._model.find_all().count()

    async def delete(self, doc_id: PydanticObjectId) -> bool:
        doc = await self._model.get(doc_id)
        if doc is None:
            return False
        await doc.delete()
        return True
