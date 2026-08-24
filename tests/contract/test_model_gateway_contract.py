from __future__ import annotations

import pytest

from tests.fakes.model import FakeModelGateway


@pytest.mark.asyncio
async def test_fake_model_is_deterministic_and_dimensionally_stable() -> None:
    model = FakeModelGateway(dimension=8)

    first = await model.embed(["alpha", "beta", "alpha"])
    repeated = await model.embed(["alpha"])
    rerank = await model.rerank("alpha beta", ["alpha", "gamma"])

    assert len(first) == 3
    assert len(first[0]) == 8
    assert first[0] == first[2] == repeated[0]
    assert rerank[0] > rerank[1]
