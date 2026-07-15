"""The fake provider must satisfy the whole DesignProvider contract, deterministically."""

from __future__ import annotations

import pytest

from app.core.errors import ProviderError
from app.design.base import DesignImage, ProviderHealth
from app.design.fake import FakeDesignProvider


@pytest.fixture
def provider() -> FakeDesignProvider:
    return FakeDesignProvider()


def _image(name: str = "shot.png", data: bytes = b"\x89PNG-data") -> DesignImage:
    return DesignImage(filename=name, media_type="image/png", data=data)


async def test_generate_from_text(provider: FakeDesignProvider) -> None:
    result = await provider.generate_from_text("a todo app")

    assert result.provider == "fake"
    assert result.external_ref.startswith("fake-text-")
    assert "a todo app" in result.html
    assert "<!doctype html>" in result.html
    assert result.css.strip()
    assert result.meta["source"] == "text"


async def test_generate_from_text_is_deterministic(provider: FakeDesignProvider) -> None:
    first = await provider.generate_from_text("same prompt")
    second = await provider.generate_from_text("same prompt")
    assert first.external_ref == second.external_ref
    assert first.html == second.html

    other = await provider.generate_from_text("different prompt")
    assert other.external_ref != first.external_ref


async def test_generate_from_image(provider: FakeDesignProvider) -> None:
    result = await provider.generate_from_image([_image("home.png")], prompt="make it dark")

    assert result.external_ref.startswith("fake-image-")
    assert "home.png" in result.html
    assert result.meta == {"source": "image", "images": ["home.png"], "prompt": "make it dark"}


async def test_generate_from_image_keys_on_pixels_not_filename(
    provider: FakeDesignProvider,
) -> None:
    same_bytes_a = await provider.generate_from_image([_image("a.png", b"identical")])
    same_bytes_b = await provider.generate_from_image([_image("a.png", b"identical")])
    different = await provider.generate_from_image([_image("a.png", b"other-pixels")])

    assert same_bytes_a.external_ref == same_bytes_b.external_ref
    assert different.external_ref != same_bytes_a.external_ref


async def test_generate_from_image_requires_an_image(provider: FakeDesignProvider) -> None:
    with pytest.raises(ProviderError):
        await provider.generate_from_image([])


async def test_refine_echoes_the_instruction_and_links_back(
    provider: FakeDesignProvider,
) -> None:
    base = await provider.generate_from_text("landing page")
    refined = await provider.refine(base.external_ref, "use a bigger hero")

    assert refined.external_ref != base.external_ref
    assert refined.external_ref.startswith(base.external_ref)
    assert "use a bigger hero" in refined.html
    assert refined.meta["refined_from"] == base.external_ref
    assert refined.meta["instruction"] == "use a bigger hero"


async def test_refine_is_chainable(provider: FakeDesignProvider) -> None:
    base = await provider.generate_from_text("p")
    once = await provider.refine(base.external_ref, "first")
    twice = await provider.refine(once.external_ref, "second")
    assert "second" in twice.html
    assert twice.meta["refined_from"] == once.external_ref


async def test_fetch_code_returns_the_generated_markup(provider: FakeDesignProvider) -> None:
    generated = await provider.generate_from_text("dashboard")
    code = await provider.fetch_code(generated.external_ref)

    assert code.html == generated.html
    assert code.css == generated.css
    assert code.assets == {}


@pytest.mark.parametrize("method", ["fetch_code", "refine"])
async def test_unknown_ref_raises_provider_error_with_a_fallback_hint(
    provider: FakeDesignProvider, method: str
) -> None:
    with pytest.raises(ProviderError) as excinfo:
        if method == "fetch_code":
            await provider.fetch_code("nope")
        else:
            await provider.refine("nope", "do a thing")

    # The hint is what phase-48 degrades on, so it must always be present.
    assert excinfo.value.fallback_hint
    assert "fake" in excinfo.value.fallback_hint


def test_capabilities(provider: FakeDesignProvider) -> None:
    caps = provider.capabilities()
    assert caps.provider == "fake"
    assert caps.from_text and caps.from_image and caps.refine and caps.fetch_code
    assert caps.max_images > 0


async def test_health(provider: FakeDesignProvider) -> None:
    assert await provider.health() == ProviderHealth.ok
