#!/usr/bin/env python3
"""生成「小雨音乐控制器」图标。

用纯标准库手写 PNG / ICO 编码，因为这台机器上的 Python 是 3.15 beta，
Pillow / cairosvg 都没有现成 wheel，也没有 ImageMagick（`convert.exe` 是
Windows 自带的磁盘转换工具，不是那个 ImageMagick）。

    python tools/make_icon.py

产物：
    frontend/public/favicon.svg      矢量源，任意尺寸都清晰
    frontend/public/favicon.ico      16/32/48/64/128/256 多尺寸
    frontend/public/icon-512.png     大图，用于 README / 桌面快捷方式
    tools/icon_preview.png           放大的自检图，不参与发布

16 / 32 用的是加粗简化字形：细字形的符干只有画布宽度的 2%，在 16px 下不到一个
像素，整个音符会糊成一块。小尺寸放大符头、去掉符尾，保住「音乐」这个意思。
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "frontend" / "public"
PREVIEW = Path(__file__).resolve().with_name("icon_preview.png")

ICO_SIZES = (16, 32, 48, 64, 128, 256)
BOLD_BELOW = 48         # 小于这个尺寸改用加粗字形
SUPERSAMPLE = 4

# 与主题同色族：theme.js 的默认强调色是 hsl(38 82% 42%)，这里取更亮的一档做渐变，
# 让小尺寸下也有足够对比度。
BG_TOP = (13, 12, 28)
BG_BOTTOM = (5, 4, 16)
DROP_TOP = (245, 181, 71)
DROP_BOTTOM = (198, 113, 16)
GLOW = (255, 184, 50)
NOTE = (14, 10, 22)

CORNER = 0.235          # 圆角半径（相对画布边长）

# ── 雨滴：圆 + 顶点构成的外凸包 ────────────────────────────────────────────
CX, CY, R = 0.5, 0.605, 0.245
APEX_Y = 0.12
_DROP_BOTTOM_Y = CY + R
_ALPHA = math.acos(R / (CY - APEX_Y))
_SIN_A, _COS_A = math.sin(_ALPHA), math.cos(_ALPHA)
T_RIGHT = (CX + R * _SIN_A, CY - R * _COS_A)
T_LEFT = (CX - R * _SIN_A, CY - R * _COS_A)

# ── 音符：符头椭圆 + 符干胶囊 +（细字形才有）渐细的符尾 ────────────────────
FLAG_STEPS = 26

NOTE_FINE = dict(
    head=(0.452, 0.676), head_rx=0.085, head_ry=0.064, head_rot=-0.34,
    stem_x=0.532, stem_top=0.448, stem_bot=0.676, stem_w=0.020,
    flag_ctrl=(0.646, 0.462), flag_end=(0.668, 0.556), flag_tip=0.26,
)
NOTE_BOLD = dict(
    head=(0.455, 0.668), head_rx=0.100, head_ry=0.078, head_rot=-0.34,
    stem_x=0.552, stem_top=0.462, stem_bot=0.668, stem_w=0.036,
    flag_ctrl=None, flag_end=None, flag_tip=0.0,
)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _mix(c1, c2, t: float):
    return tuple(_lerp(c1[i], c2[i], t) for i in range(3))


def _bezier(p0, p1, p2, t: float):
    mt = 1.0 - t
    return (
        mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0],
        mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1],
    )


def _in_round_square(u: float, v: float, rad: float) -> bool:
    dx = max(abs(u - 0.5) - (0.5 - rad), 0.0)
    dy = max(abs(v - 0.5) - (0.5 - rad), 0.0)
    return dx * dx + dy * dy <= rad * rad


def _in_triangle(px, py, a, b, c) -> bool:
    def cross(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])

    d1, d2, d3 = cross((px, py), a, b), cross((px, py), b, c), cross((px, py), c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def in_drop(u: float, v: float) -> bool:
    dx, dy = u - CX, v - CY
    if dx * dx + dy * dy <= R * R:
        return True
    return _in_triangle(u, v, (CX, APEX_Y), T_RIGHT, T_LEFT)


def _in_ellipse(px, py, cx, cy, rx, ry, rot) -> bool:
    dx, dy = px - cx, py - cy
    c, s = math.cos(-rot), math.sin(-rot)
    u, v = dx * c - dy * s, dx * s + dy * c
    return (u / rx) ** 2 + (v / ry) ** 2 <= 1.0


def _dist_seg(px, py, ax, ay, bx, by) -> float:
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    l2 = vx * vx + vy * vy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / l2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def flag_stamps(note) -> list[tuple[float, float, float]]:
    """沿符尾曲线取样，作为一串圆形笔触——比追踪轮廓简单，且支持渐细。"""
    if not note["flag_ctrl"]:
        return []
    out = []
    for i in range(FLAG_STEPS + 1):
        t = i / FLAG_STEPS
        px, py = _bezier((note["stem_x"], note["stem_top"]), note["flag_ctrl"],
                         note["flag_end"], t)
        out.append((px, py, _lerp(note["stem_w"], note["stem_w"] * note["flag_tip"], t)))
    return out


NOTE_FLAGS = {id(n): flag_stamps(n) for n in (NOTE_FINE, NOTE_BOLD)}


def in_note(u: float, v: float, note) -> bool:
    hx, hy = note["head"]
    if _in_ellipse(u, v, hx, hy, note["head_rx"], note["head_ry"], note["head_rot"]):
        return True
    if _dist_seg(u, v, note["stem_x"], note["stem_top"],
                 note["stem_x"], note["stem_bot"]) <= note["stem_w"]:
        return True
    for fx, fy, fr in NOTE_FLAGS[id(note)]:
        if (u - fx) ** 2 + (v - fy) ** 2 <= fr * fr:
            return True
    return False


def _sample(u: float, v: float, note):
    """返回 (r, g, b, a)，a 为 0..1。"""
    if not _in_round_square(u, v, CORNER):
        return (0, 0, 0, 0.0)

    color = _mix(BG_TOP, BG_BOTTOM, v)
    dist = math.hypot(u - CX, v - CY + 0.05)
    glow = max(0.0, 1.0 - dist / 0.60) ** 2 * 0.55
    if glow > 0:
        color = _mix(color, GLOW, glow)

    if in_drop(u, v):
        t = (v - APEX_Y) / (_DROP_BOTTOM_Y - APEX_Y)
        color = _mix(DROP_TOP, DROP_BOTTOM, min(1.0, max(0.0, t)))
        if in_note(u, v, note):
            color = NOTE
    return (color[0], color[1], color[2], 1.0)


def render_rgba(size: int, note=NOTE_FINE) -> bytearray:
    """超采样渲染，边缘靠覆盖率得到抗锯齿。"""
    buf = bytearray(size * size * 4)
    step = 1.0 / size
    total = SUPERSAMPLE * SUPERSAMPLE
    off = 0
    for y in range(size):
        for x in range(size):
            r = g = b = a = 0.0
            for sy in range(SUPERSAMPLE):
                v = (y + (sy + 0.5) / SUPERSAMPLE) * step
                for sx in range(SUPERSAMPLE):
                    u = (x + (sx + 0.5) / SUPERSAMPLE) * step
                    sr, sg, sb, sa = _sample(u, v, note)
                    r += sr * sa
                    g += sg * sa
                    b += sb * sa
                    a += sa
            if a > 0:
                buf[off + 0] = min(255, int(r / a + 0.5))
                buf[off + 1] = min(255, int(g / a + 0.5))
                buf[off + 2] = min(255, int(b / a + 0.5))
                buf[off + 3] = min(255, int(a / total * 255 + 0.5))
            off += 4
    return buf


def downsample(src: bytearray, src_size: int, dst_size: int) -> bytearray:
    """盒式降采样（渲染时已超采样，这里只做平均）。"""
    factor = src_size // dst_size
    dst = bytearray(dst_size * dst_size * 4)
    area = factor * factor
    for y in range(dst_size):
        for x in range(dst_size):
            r = g = b = a = 0
            for dy in range(factor):
                base = ((y * factor + dy) * src_size + x * factor) * 4
                for dx in range(factor):
                    o = base + dx * 4
                    r += src[o]
                    g += src[o + 1]
                    b += src[o + 2]
                    a += src[o + 3]
            o = (y * dst_size + x) * 4
            dst[o] = r // area
            dst[o + 1] = g // area
            dst[o + 2] = b // area
            dst[o + 3] = a // area
    return dst


def encode_png(width: int, height: int, rgba: bytearray) -> bytes:
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: None
        raw += rgba[y * stride : (y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def encode_ico(images: list[tuple[int, bytes]]) -> bytes:
    """ICO 容器；Vista 起支持直接内嵌 PNG 数据，省掉 BMP + AND 掩码。"""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # 256 在 ICO 目录里记为 0
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    return header + entries + blobs


# ── SVG：用同一套几何生成，避免和位图不一致 ────────────────────────────────
def _poly(points, scale: float) -> str:
    pts = " ".join(f"{p[0] * scale:.2f},{p[1] * scale:.2f}" for p in _same_winding(points))
    return f"M{pts}Z"


def _same_winding(points):
    """统一绕向。

    音符由椭圆 / 符干 / 符尾三段拼成，合成一条 path 靠 fill-rule="nonzero" 填成并集。
    两段绕向相反时重叠处绕数相加为 0，会在符干与符头交接处长出一个洞。这里按有向面积
    把所有子路径归到同一绕向，重叠处就只会是 +1+1 而不是 +1-1。
    """
    area = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return points if area >= 0 else list(reversed(points))


def _ellipse_points(cx, cy, rx, ry, rot, steps=48):
    c, s = math.cos(rot), math.sin(rot)
    out = []
    for i in range(steps):
        a = 2 * math.pi * i / steps
        px, py = rx * math.cos(a), ry * math.sin(a)
        out.append((cx + px * c - py * s, cy + px * s + py * c))
    return out


def _capsule_points(ax, ay, bx, by, w, steps=12):
    """胶囊轮廓：两侧直边 + 两端半圆。y 轴向下，端盖沿 -y 方向外凸。"""
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / L * w, dx / L * w
    left, right = [], []
    for i in range(steps + 1):
        t = i / steps
        px, py = _lerp(ax, bx, t), _lerp(ay, by, t)
        left.append((px + nx, py + ny))
        right.append((px - nx, py - ny))
    # 起点端盖从右侧绕到左侧，终点端盖绕回来，两段都走远离另一端的半圆。
    cap_a = [(ax + w * math.cos(-math.pi * i / steps), ay + w * math.sin(-math.pi * i / steps))
             for i in range(steps + 1)]
    cap_b = [(bx + w * math.cos(math.pi - math.pi * i / steps),
              by + w * math.sin(math.pi - math.pi * i / steps))
             for i in range(steps + 1)]
    return left + cap_b + list(reversed(right)) + cap_a


def _flag_points(note):
    """沿曲线两侧偏移出闭合轮廓，得到渐细的符尾。"""
    raw = flag_stamps(note)
    if not raw:
        return []
    left, right = [], []
    for i, (px, py, r) in enumerate(raw):
        j = min(i, len(raw) - 2)
        tx = raw[j + 1][0] - raw[j][0]
        ty = raw[j + 1][1] - raw[j][1]
        L = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / L * r, tx / L * r
        left.append((px + nx, py + ny))
        right.append((px - nx, py - ny))

    # 末端补一段圆帽。位图是按圆形笔触盖章的，末端为圆；多边形若直接首尾相连会把
    # 圆头切平，512px 下差约 2.7px，两条路径就对不上了。
    ex, ey, er = raw[-1]
    a0 = math.atan2(left[-1][1] - ey, left[-1][0] - ex)
    a1 = math.atan2(right[-1][1] - ey, right[-1][0] - ex)
    forward = math.atan2(raw[-1][1] - raw[-2][1], raw[-1][0] - raw[-2][0])
    span = (a1 - a0) % (2 * math.pi)
    # 走包含前进方向的半圆，否则圆帽会扣到曲线内侧、和主体自交。
    if ((forward - a0) % (2 * math.pi)) > span:
        span -= 2 * math.pi
    cap = [
        (ex + er * math.cos(a0 + span * i / 12), ey + er * math.sin(a0 + span * i / 12))
        for i in range(1, 12)
    ]
    return left + cap + list(reversed(right))


def build_svg(scale: int = 512) -> str:
    note = NOTE_FINE
    r = R * scale
    drop = (
        f"M{CX * scale:.1f},{APEX_Y * scale:.1f} "
        f"L{T_RIGHT[0] * scale:.1f},{T_RIGHT[1] * scale:.1f} "
        f"A{r:.1f},{r:.1f} 0 1,1 {T_LEFT[0] * scale:.1f},{T_LEFT[1] * scale:.1f} Z"
    )
    # 所有子路径合成一条 path：同一颜色填充，分开写会在重叠处露出发丝缝。
    hx, hy = note["head"]
    note_d = " ".join(
        (
            _poly(_ellipse_points(hx, hy, note["head_rx"], note["head_ry"], note["head_rot"]), scale),
            _poly(_capsule_points(note["stem_x"], note["stem_top"],
                                  note["stem_x"], note["stem_bot"], note["stem_w"]), scale),
            _poly(_flag_points(note), scale) or "",
        )
    ).strip()
    hexed = lambda c: "#%02x%02x%02x" % c
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {scale} {scale}" \
width="{scale}" height="{scale}" role="img" aria-label="小雨音乐控制器">
  <defs>
    <linearGradient id="xy-bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{hexed(BG_TOP)}"/>
      <stop offset="1" stop-color="{hexed(BG_BOTTOM)}"/>
    </linearGradient>
    <linearGradient id="xy-drop" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{hexed(DROP_TOP)}"/>
      <stop offset="1" stop-color="{hexed(DROP_BOTTOM)}"/>
    </linearGradient>
    <radialGradient id="xy-glow" cx="50%" cy="68%" r="62%">
      <stop offset="0" stop-color="{hexed(GLOW)}" stop-opacity=".55"/>
      <stop offset="1" stop-color="{hexed(GLOW)}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{scale}" height="{scale}" rx="{CORNER * scale:.0f}" fill="url(#xy-bg)"/>
  <rect width="{scale}" height="{scale}" rx="{CORNER * scale:.0f}" fill="url(#xy-glow)"/>
  <path d="{drop}" fill="url(#xy-drop)"/>
  <path d="{note_d}" fill="{hexed(NOTE)}" fill-rule="nonzero"/>
</svg>
"""


def _nearest(rgba: bytearray, size: int, factor: int) -> tuple[int, bytearray]:
    out_size = size * factor
    out = bytearray(out_size * out_size * 4)
    for y in range(out_size):
        sy = y // factor
        for x in range(out_size):
            sx = x // factor
            s = (sy * size + sx) * 4
            d = (y * out_size + x) * 4
            out[d : d + 4] = rgba[s : s + 4]
    return out_size, out


def build_preview(sizes: dict[int, bytearray], magnify: int = 6) -> bytes:
    """把小尺寸放大拼在一起，用来肉眼检查音符在 16/32 下还认不认得出。"""
    tiles = [(s, *_nearest(sizes[s], s, magnify)) for s in (16, 32, 48, 64)]
    gap = 24
    width = sum(t[1] for t in tiles) + gap * (len(tiles) + 1)
    height = max(t[1] for t in tiles) + gap * 2
    canvas = bytearray(width * height * 4)
    for i in range(width * height):
        canvas[i * 4 : i * 4 + 4] = bytes((24, 24, 32, 255))

    x = gap
    for _, size, rgba in tiles:
        for row in range(size):
            src = row * size * 4
            dst = ((gap + row) * width + x) * 4
            canvas[dst : dst + size * 4] = rgba[src : src + size * 4]
        x += size + gap
    return encode_png(width, height, canvas)


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)

    # 大尺寸共用一次 1024×1024 渲染再降采样，比逐尺寸重渲染快得多，且天然一致。
    base_size = 256 * SUPERSAMPLE
    big = render_rgba(base_size, NOTE_FINE)
    sizes = {
        size: downsample(big, base_size, size)
        for size in (*ICO_SIZES, 512)
        if size >= BOLD_BELOW
    }
    # 小尺寸用加粗字形单独渲染。它们本身很小，超采样开高一点也几乎不花时间。
    for size in [s for s in ICO_SIZES if s < BOLD_BELOW]:
        hi = size * 8
        sizes[size] = downsample(render_rgba(hi, NOTE_BOLD), hi, size)

    (PUBLIC / "favicon.svg").write_text(build_svg(), encoding="utf-8")
    (PUBLIC / "icon-512.png").write_bytes(encode_png(512, 512, sizes[512]))
    (PUBLIC / "favicon.ico").write_bytes(
        encode_ico([(s, encode_png(s, s, sizes[s])) for s in ICO_SIZES])
    )
    PREVIEW.write_bytes(build_preview(sizes))

    for name in ("favicon.svg", "favicon.ico", "icon-512.png"):
        print(f"  {name:<16} {(PUBLIC / name).stat().st_size:>7,} bytes")
    print(f"  {'(preview)':<16} {PREVIEW.stat().st_size:>7,} bytes  -> {PREVIEW}")


if __name__ == "__main__":
    main()
