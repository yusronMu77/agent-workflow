"""Bounded PNG RGBA8 inspection for benchmark artifacts, using only stdlib."""
import struct
import zlib


def inspect_png(body):
    if body[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", body[16:29])
    if not (0 < width <= 4096 and 0 < height <= 4096 and depth == 8 and color == 6
            and compression == filtering == interlace == 0):
        raise ValueError("Requires bounded non-interlaced PNG RGBA8")
    data, offset = bytearray(), 8
    while offset < len(body):
        length = struct.unpack(">I", body[offset:offset + 4])[0]
        kind = body[offset + 4:offset + 8]
        chunk = body[offset + 8:offset + 8 + length]
        if len(chunk) != length or offset + 12 + length > len(body):
            raise ValueError("Truncated PNG chunk")
        checksum = struct.unpack(">I", body[offset + 8 + length:offset + 12 + length])[0]
        if zlib.crc32(kind + chunk) & 0xffffffff != checksum:
            raise ValueError("PNG CRC mismatch")
        if kind == b"IDAT":
            data.extend(chunk)
        offset += length + 12
        if kind == b"IEND":
            break
    expected = height * (width * 4 + 1)
    stream = zlib.decompressobj()
    raw = stream.decompress(data, expected + 1)
    if len(raw) != expected or not stream.eof:
        raise ValueError("Invalid or oversized PNG data")
    previous = bytearray(width * 4)
    alpha_min, alpha_max = 255, 0
    stride = width * 4 + 1
    for index in range(height):
        kind = raw[index * stride]
        row = bytearray(raw[index * stride + 1:(index + 1) * stride])
        for column in range(len(row)):
            left = row[column - 4] if column >= 4 else 0
            up = previous[column]
            upper_left = previous[column - 4] if column >= 4 else 0
            if kind == 1:
                value = left
            elif kind == 2:
                value = up
            elif kind == 3:
                value = (left + up) // 2
            elif kind == 4:
                p = left + up - upper_left
                a, b, c = abs(p - left), abs(p - up), abs(p - upper_left)
                value = left if a <= b and a <= c else up if b <= c else upper_left
            elif kind == 0:
                value = 0
            else:
                raise ValueError("Unsupported PNG filter")
            row[column] = (row[column] + value) & 255
        alpha_min = min(alpha_min, min(row[3::4]))
        alpha_max = max(alpha_max, max(row[3::4]))
        previous = row
    return {"width": width, "height": height, "depth": depth, "color": color,
            "alpha_min": alpha_min / 255, "alpha_max": alpha_max / 255}
