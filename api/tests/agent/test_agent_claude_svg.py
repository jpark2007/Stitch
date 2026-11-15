"""ClaudeSvgImageModel, its sanitizer and rasterizer, and IMAGE_PROVIDER=claude. All offline: the
Claude client is a fake with a ``messages.create``; no real request is made."""

from __future__ import annotations

import base64
import io
import logging
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from PIL import Image

from agent.adapters import AlphaAwareRemover, ClaudeSvgImageModel, choose_image_provider
from agent.adapters import claude_svg as svg
from agent.tools.art import ArtService, ImageModel, accepts_plain_background, build_art_prompt

KEY = "sk-ant-api03-SVGtestKEYvalue0001"
NS = 'xmlns="http://www.w3.org/2000/svg"'

GOOD_SVG = f"""<svg {NS} viewBox="0 0 100 100">
<defs>
<filter id="paper"><feTurbulence type="fractalNoise" baseFrequency="0.9" result="n"/>
<feComposite in="n" in2="SourceGraphic" operator="in" result="g"/>
<feMerge><feMergeNode in="SourceGraphic"/><feMergeNode in="g"/></feMerge>
<feDropShadow dx="1" dy="1.5" stdDeviation="1" flood-color="#3b2a1e" flood-opacity="0.3"/></filter>
</defs>
<g filter="url(#paper)">
<path id="body" d="M30 30 L71 31 L70 70 L29 69 Z" fill="#b5523b"/>
<path d="M35 35 L64 36 L63 45 L36 44 Z" fill="#d9a441"/>
</g>
</svg>"""


def response(text: str, stop: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=2600, output_tokens=3000),
    )


class FakeClient:
    """``messages.create`` answering from a script: a response, or an exception to raise."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **request: Any) -> Any:
        self.requests.append(request)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item


def model(*script: Any, **kwargs: Any) -> tuple[ClaudeSvgImageModel, FakeClient, list[float]]:
    client = FakeClient(*script)
    sleeps: list[float] = []
    m = ClaudeSvgImageModel(client=client, effort=kwargs.pop("effort", "medium"), sleep=sleeps.append, **kwargs)
    return m, client, sleeps


def open_png(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def png(size: tuple[int, int] = (64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 80, 40)).save(buf, format="PNG")
    return buf.getvalue()


def request_error() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(status: int) -> anthropic.APIStatusError:
    resp = httpx.Response(status, request=request_error(), json={"error": {"message": "busy"}})
    return anthropic.APIStatusError("busy", response=resp, body=None)


# ------------------------------------------------------------------- the image


def test_it_is_an_image_model_with_the_plain_background_keyword() -> None:
    m, _, _ = model(response(GOOD_SVG))
    assert isinstance(m, ImageModel)
    assert accepts_plain_background(m)


def test_a_good_svg_becomes_an_rgba_png_of_the_exact_size_with_a_transparent_corner() -> None:
    m, _, _ = model(response(GOOD_SVG))
    im = open_png(m.generate("a prop", size=(300, 200)))
    assert im.size == (300, 200) and im.mode == "RGBA"
    assert im.getpixel((0, 0))[3] == 0
    # 100x100 viewBox letterboxed into 300x200: 200x200, bottom-centered, never stretched.
    bbox = im.getchannel("A").getbbox()
    assert bbox is not None and 50 <= bbox[0] and bbox[2] <= 250
    assert im.getpixel((150, 100))[3] == 255


def test_the_back_layer_is_opaque_and_fills_the_frame() -> None:
    m, client, _ = model(response(GOOD_SVG))
    im = open_png(m.generate("a backdrop", size=(400, 200), plain_background=False))
    assert im.size == (400, 200)
    assert im.getchannel("A").getextrema() == (255, 255)
    assert im.getpixel((0, 0))[:3] == svg.BACKDROP_COLOR
    assert "opaque" in client.requests[0]["messages"][0]["content"][-1]["text"]


@pytest.mark.parametrize(
    "reply",
    [
        f"```svg\n{GOOD_SVG}\n```",
        f"Here is the canoe you asked for:\n\n{GOOD_SVG}\n\nI hope it fits the book.",
        f'<?xml version="1.0" encoding="UTF-8"?>\n{GOOD_SVG}',
    ],
)
def test_fenced_and_prose_wrapped_replies_parse(reply: str) -> None:
    m, _, _ = model(response(reply))
    assert open_png(m.generate("a prop", size=(100, 100))).size == (100, 100)


def test_the_request_is_one_plain_messages_call() -> None:
    m, client, _ = model(response(GOOD_SVG), effort="high")
    m.generate("PROMPT TEXT", size=(2048, 464))
    req = client.requests[0]
    assert req["model"] == m.model
    assert req["max_tokens"] == 16000
    assert req["output_config"] == {"effort": "high"}
    assert req["system"] == svg.SYSTEM_PROMPT
    for forbidden in ("thinking", "temperature", "top_p", "top_k", "tool_choice", "tools"):
        assert forbidden not in req
    messages = req["messages"]
    assert [msg["role"] for msg in messages] == ["user"]  # no assistant prefill
    text = messages[0]["content"][-1]["text"]
    assert text.startswith("PROMPT TEXT")
    assert 'viewBox="0 0 2048 464"' in text and "transparent" in text


def test_model_and_effort_come_from_the_env(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv(svg.MODEL_ENV, "  claude-sonnet-5-5 ")
    monkeypatch.setenv(svg.EFFORT_ENV, "XHIGH")
    m = ClaudeSvgImageModel(client=FakeClient(response(GOOD_SVG)))
    assert (m.model, m.effort) == ("claude-sonnet-5-5", "xhigh")
    monkeypatch.setenv(svg.EFFORT_ENV, "turbo")
    monkeypatch.delenv(svg.MODEL_ENV)
    with caplog.at_level(logging.WARNING):
        m = ClaudeSvgImageModel(client=FakeClient(response(GOOD_SVG)))
    assert m.effort == "medium" and m.model == "claude-opus-5-5"
    assert "turbo" in caplog.text


def test_reference_images_reach_the_request() -> None:
    m, client, _ = model(response(GOOD_SVG))
    m.generate("the lid", reference_images=[png((80, 60))], size=(100, 100))
    content = client.requests[0]["messages"][0]["content"]
    images = [block for block in content if block["type"] == "image"]
    assert len(images) == 1
    assert images[0]["source"]["media_type"] == "image/png"
    assert open_png(base64.b64decode(images[0]["source"]["data"])).size == (80, 60)
    assert content[-1]["type"] == "text"


# ------------------------------------------------------------------- stop reasons and retries


@pytest.mark.parametrize(("stop", "needle"), [("refusal", "declined"), ("max_tokens", "cut off")])
def test_refusal_and_max_tokens_raise(stop: str, needle: str) -> None:
    m, client, _ = model(response(GOOD_SVG, stop=stop))
    with pytest.raises(RuntimeError, match=needle):
        m.generate("a prop", size=(100, 100))
    assert len(client.requests) == 1  # not retried


def test_a_reply_without_svg_raises() -> None:
    m, _, _ = model(response("I would rather not."))
    with pytest.raises(RuntimeError, match="no <svg>"):
        m.generate("a prop", size=(100, 100))


def test_retries_are_bounded() -> None:
    m, client, sleeps = model(status_error(529), status_error(503), response(GOOD_SVG))
    with pytest.raises(anthropic.APIStatusError):
        m.generate("a prop", size=(100, 100))
    assert len(client.requests) == 2 and len(sleeps) == 1  # one try plus max_retries=1
    assert all(wait <= 8 for wait in sleeps)

    m, client, sleeps = model(anthropic.APIConnectionError(request=request_error()), response(GOOD_SVG))
    assert open_png(m.generate("a prop", size=(100, 100))).size == (100, 100)
    assert len(client.requests) == 2

    m, client, _ = model(anthropic.APITimeoutError(request=request_error()), response(GOOD_SVG))
    with pytest.raises(anthropic.APITimeoutError):
        m.generate("a prop", size=(100, 100))
    assert len(client.requests) == 1  # a timeout is never retried

    m, client, _ = model(status_error(400), response(GOOD_SVG))
    with pytest.raises(anthropic.APIStatusError):
        m.generate("a prop", size=(100, 100))
    assert len(client.requests) == 1


def test_the_worst_case_is_stated() -> None:
    m, _, _ = model(response(GOOD_SVG))
    assert (m.timeout, m.max_retries) == (150.0, 1)
    assert m.worst_case_s == 308.0


def test_without_a_key_or_client_it_refuses_to_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        ClaudeSvgImageModel()


# ------------------------------------------------------------------- the sanitizer


def sanitized(body: str, root_attrs: str = 'viewBox="0 0 100 100"') -> str:
    text, _ = svg.sanitize_svg(f'<svg {NS} xmlns:xlink="http://www.w3.org/1999/xlink" {root_attrs}>{body}'
                               '<path d="M10 10 L90 10 L90 90 Z" fill="#b5523b"/></svg>')
    return text


def test_script_and_event_handlers_are_removed() -> None:
    out = sanitized('<script>alert(1)</script><g onclick="alert(2)"><rect width="5" height="5"/></g>',
                    'viewBox="0 0 100 100" onload="alert(3)"')
    assert "script" not in out and "alert" not in out and "onload" not in out and "onclick" not in out
    assert "<rect" in out


def test_external_hrefs_are_removed_and_fragments_kept() -> None:
    out = sanitized(
        '<defs><path id="p" d="M0 0 L5 5 Z"/></defs>'
        '<use href="https://evil.example/x.svg#a"/><use xlink:href="javascript:alert(1)"/>'
        '<use href="#p"/><use xlink:href="#p"/>'
    )
    assert "evil" not in out and "javascript" not in out
    assert out.count("#p") == 2


def test_image_foreign_object_and_text_are_removed() -> None:
    out = sanitized(
        '<image href="http://evil.example/a.png" width="10" height="10"/>'
        '<image href="data:image/png;base64,AAAA"/>'
        '<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">hi</div></foreignObject>'
        '<text x="1" y="1">MOM<tspan>DAD</tspan></text><textPath>X</textPath>'
        '<style>@import url(http://evil.example/a.css);</style>'
    )
    for gone in ("image", "foreignObject", "text", "tspan", "MOM", "DAD", "style", "evil"):
        assert gone not in out, gone


def test_entity_and_doctype_declarations_are_refused() -> None:
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        f'<svg {NS} viewBox="0 0 10 10"><path d="M0 0 L5 5 Z"/><desc>&b;</desc></svg>'
    )
    with pytest.raises(RuntimeError, match="DOCTYPE"):
        svg.sanitize_svg(bomb)
    external = f'<!DOCTYPE svg SYSTEM "file:///etc/passwd"><svg {NS}><path d="M0 0 L5 5 Z"/></svg>'
    with pytest.raises(RuntimeError, match="DOCTYPE"):
        svg.sanitize_svg(external)


def test_non_fragment_urls_are_removed() -> None:
    out = sanitized(
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#b5523b"/></linearGradient></defs>'
        '<rect width="5" height="5" fill="url(#g)" filter="url(http://evil.example/f.svg#x)"'
        ' style="fill:url(file:///etc/passwd);opacity:0.5"/>'
        "<rect width=\"5\" height=\"5\" mask=\"url('data:image/svg+xml,AAAA')\"/>"
    )
    assert 'fill="url(#g)"' in out
    assert "evil" not in out and "passwd" not in out and "data:" not in out
    assert "opacity:0.5" in out


def test_an_svg_with_nothing_drawable_left_raises() -> None:
    with pytest.raises(RuntimeError, match="nothing drawable"):
        svg.sanitize_svg(f'<svg {NS} viewBox="0 0 10 10"><text>HELLO</text><image href="a.png"/></svg>')
    with pytest.raises(RuntimeError, match="nothing drawable"):
        svg.sanitize_svg(f'<svg {NS}><defs><path id="p" d="M0 0 L5 5 Z"/></defs></svg>')


def test_the_size_is_capped() -> None:
    pad = "<!--" + "x" * svg.MAX_SVG_BYTES + "-->"
    with pytest.raises(RuntimeError, match="bytes"):
        svg.sanitize_svg(f'<svg {NS}>{pad}<path d="M0 0 L5 5 Z"/></svg>')


def test_malformed_xml_and_a_wrong_root_raise() -> None:
    with pytest.raises(RuntimeError, match="well-formed"):
        svg.sanitize_svg(f'<svg {NS}><path d="M0 0"></svg>')
    with pytest.raises(RuntimeError, match="root"):
        svg.sanitize_svg('<html><svg><path d="M0 0 L5 5 Z"/></svg></html>')


def test_the_viewbox_comes_from_width_and_height_or_the_requested_size() -> None:
    _, view = svg.sanitize_svg(f'<svg {NS} width="400px" height="100"><path d="M0 0 L5 5 Z"/></svg>')
    assert view == (400.0, 100.0)
    _, view = svg.sanitize_svg(f'<svg {NS} width="100%"><path d="M0 0 L5 5 Z"/></svg>', (2048, 192))
    assert view == (2048.0, 192.0)
    # No namespace on the input still renders as SVG.
    text, _ = svg.sanitize_svg('<svg viewBox="0 0 10 10"><path d="M0 0 L9 0 L9 9 Z"/></svg>')
    assert 'xmlns="http://www.w3.org/2000/svg"' in text


def test_a_render_with_no_visible_pixel_raises() -> None:
    text, view = svg.sanitize_svg(f'<svg {NS} viewBox="0 0 10 10"><path d="M0 0 L5 5 Z" fill="none"/></svg>')
    with pytest.raises(RuntimeError, match="empty"):
        svg.rasterize(text, view, (50, 50), plain_background=True)


def test_a_wide_back_layer_is_cropped_not_stretched() -> None:
    # A square drawing for a 4:1 layer: covered and cropped bottom-anchored, every pixel opaque.
    body = '<rect x="0" y="0" width="100" height="50" fill="#4f8a8b"/><rect x="0" y="50" width="100" height="50" fill="#7d8a4a"/>'
    text, view = svg.sanitize_svg(f'<svg {NS} viewBox="0 0 100 100">{body}</svg>')
    im = open_png(svg.rasterize(text, view, (400, 100), plain_background=False))
    assert im.size == (400, 100)
    assert im.getpixel((200, 90))[:3] == (0x7D, 0x8A, 0x4A)  # the bottom band is what is kept


# ------------------------------------------------------------------- through ArtService


def test_make_cutout_with_the_real_remover_is_cropped_to_the_shape() -> None:
    m, _, _ = model(response(GOOD_SVG))
    art = ArtService(m, AlphaAwareRemover())
    cutout = art.make_cutout(build_art_prompt("a red wooden canoe", kind="prop"), size=(400, 400))
    assert cutout is not None
    w, h = open_png(cutout.png).size
    # The drawing covers about 41% of the 100-unit viewBox (plus a small shadow) of a 400 px canvas.
    assert 150 <= w <= 190 and 150 <= h <= 195
    assert cutout.bbox[0] > 100 and cutout.bbox[2] < 300
    assert cutout.anchor[1] == 1.0


def test_a_failure_becomes_placeholder_art() -> None:
    m, _, _ = model(response(GOOD_SVG, stop="refusal"))
    assert ArtService(m, AlphaAwareRemover()).make_cutout("x") is None


def test_make_layer_back_is_opaque_through_the_service() -> None:
    m, _, _ = model(response(GOOD_SVG))
    layer = ArtService(m, AlphaAwareRemover()).make_layer("x", layer="back", size=(256, 128))
    assert layer is not None
    assert open_png(layer.png).getchannel("A").getextrema() == (255, 255)


# ------------------------------------------------------------------- provider selection


def test_explicit_claude_with_the_key_is_chosen() -> None:
    choice = choose_image_provider({"IMAGE_PROVIDER": "Claude", "ANTHROPIC_API_KEY": KEY})
    assert choice.provider == "claude" and choice.warning is None


def test_explicit_claude_without_the_key_is_placeholder_with_the_reason() -> None:
    choice = choose_image_provider({"IMAGE_PROVIDER": "claude", "IMAGE_API_KEY": "sk-x"})
    assert choice.provider == "placeholder"
    assert "ANTHROPIC_API_KEY is not set" in choice.reason


@pytest.mark.parametrize(
    ("env", "provider"),
    [
        ({"ANTHROPIC_API_KEY": KEY}, "placeholder"),
        ({"ANTHROPIC_API_KEY": KEY, "IMAGE_API_KEY": "sk-x"}, "openai"),
        ({"ANTHROPIC_API_KEY": KEY, "HIGGSFIELD_API_KEY": "hf"}, "higgsfield"),
        ({"ANTHROPIC_API_KEY": KEY, "CLOUDFLARE_ACCOUNT_ID": "a" * 32, "CLOUDFLARE_API_TOKEN": "t" * 40}, "cloudflare"),
        ({"ANTHROPIC_API_KEY": KEY, "IMAGE_PROVIDER": "claud"}, "placeholder"),
    ],
)
def test_the_unset_rule_never_picks_claude(env: dict[str, str], provider: str) -> None:
    assert choose_image_provider(env).provider == provider
