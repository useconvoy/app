"""Hostile visual inputs are bounded before an image decoder or model is called."""

import base64
import struct
import zlib

import pytest
from convoy_contracts.execution import (
    MAX_PNG_BYTES,
    VISUAL_INSTRUCTION,
    VISUAL_PROFILE,
    validate_observation,
    visual_png_bytes,
)


def png(*, width=480, height=480, raw=None):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    data = raw if raw is not None else bytes(480 * (480 * 3 + 1))
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) +
                            chunk(b"IDAT", zlib.compress(data)) + chunk(b"IEND", b"")).decode()


def test_visual_pixels_state_and_instruction_are_a_distinct_exact_profile():
    value = {"image_png_base64": png(), "state": [0.0] * 4, "instruction": VISUAL_INSTRUCTION}
    assert validate_observation(value, VISUAL_PROFILE) is value
    with pytest.raises(ValueError):
        validate_observation(value)
    for changed in ({**value, "state": [0.0] * 39}, {**value, "state": [float("nan")] * 4},
                    {**value, "instruction": "other task"}, {**value, "extra": 1}):
        with pytest.raises(ValueError):
            validate_observation(changed, VISUAL_PROFILE)


def test_png_limits_dimensions_crc_and_inflated_size_before_library_decode():
    for invalid in ("!", "A" * (MAX_PNG_BYTES * 2), png(width=1000000), png(height=1000000),
                    png(raw=b"\0" * (480 * (480 * 3 + 1) + 1)),
                    png(raw=b"\xff" * (480 * (480 * 3 + 1))), png()[:-4]):
        with pytest.raises(ValueError):
            visual_png_bytes(invalid)
