"""Transient food-photo analysis; never persist the image or model response."""

import base64
import io
import json
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from app.rag.generator import LLMReply, _usage
from app.schemas import PhotoEstimate

MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 20_000_000
FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}

PHOTO_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "grams": {"type": "number"},
                    "kcal": {"type": "number"},
                    "protein_g": {"type": ["number", "null"]},
                    "carbs_g": {"type": ["number", "null"]},
                    "fat_g": {"type": ["number", "null"]},
                    "fiber_g": {"type": ["number", "null"]},
                },
                "required": ["name", "grams", "kcal", "protein_g", "carbs_g", "fat_g", "fiber_g"],
                "additionalProperties": False,
            },
        },
        "uncertainty": {"type": "string"},
    },
    "required": ["items", "uncertainty"],
    "additionalProperties": False,
}


def prepare_photo(data: bytes, content_type: str) -> bytes:
    if not data or len(data) > MAX_PHOTO_BYTES or content_type not in FORMATS:
        raise ValueError("Use a JPEG, PNG or WebP image up to 5 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != FORMATS[content_type] or image.width * image.height > MAX_PIXELS:
                    raise ValueError("Image type or dimensions are invalid.")
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1024, 1024))
                image = image.convert("RGB")
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=85)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Invalid or oversized image.") from exc


def analyze_photo(coach, image: bytes) -> tuple[PhotoEstimate, LLMReply]:
    encoded = base64.b64encode(image).decode("ascii")
    response = coach.openai.responses.create(
        model=coach.models["fast"],
        store=False,
        max_output_tokens=2500,
        reasoning={"effort": "low"},
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": (
                    "Estimate visible food components, portions in grams and nutrition for those portions. "
                    "Use Turkish names. These are uncertain estimates, not measurements. Mention uncertainty, "
                    "hidden oil/sauces and ambiguous items in Turkish. Do not invent invisible ingredients. "
                    "Return no items if there is no recognizable food. Use null for unknown macros."
                )},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encoded}", "detail": "low"},
            ],
        }],
        text={"format": {"type": "json_schema", "name": "photo_food", "schema": PHOTO_SCHEMA, "strict": True}},
    )
    try:
        estimate = PhotoEstimate.model_validate(json.loads(response.output_text))
    except (ValueError, ValidationError, TypeError) as exc:
        raise ValueError("Photo analysis returned an invalid result; try again.") from exc
    return estimate, LLMReply(text="", usage=_usage(coach.models["fast"], response.usage))
