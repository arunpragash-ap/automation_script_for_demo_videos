import os

FONT_CANDIDATES = [
    ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
    ("/System/Library/Fonts/Helvetica.ttc", 1),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
    ("/Library/Fonts/Arial Bold.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
]


def pil():
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
    return Image, ImageDraw, ImageFilter, ImageFont, ImageOps


def find_font(custom=None):
    if custom:
        return (custom, 0) if os.path.exists(custom) else None
    for path, index in FONT_CANDIDATES:
        if os.path.exists(path):
            return path, index
    return None


def wrap(text, font, max_width):
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if line and font.getlength(trial) > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines or [""]


def render_title_card(blank_path, title, size, out, font_spec):
    Image, ImageDraw, ImageFilter, ImageFont, ImageOps = pil()
    width, height = size
    base = ImageOps.fit(Image.open(blank_path).convert("RGBA"), size, Image.LANCZOS)
    px = max(int(round(0.08 * min(width, height))), 12)
    font = ImageFont.truetype(font_spec[0], px, index=font_spec[1])
    margin = int(width * 0.104)
    lines = wrap(title, font, width - 2 * margin)
    line_h = int(px * 1.25)
    cap = -font.getbbox("H", anchor="ls")[1]
    top = (height - (cap + line_h * (len(lines) - 1))) / 2

    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for i, line in enumerate(lines):
        draw.text((margin, top + cap + i * line_h), line, font=font, fill=(255, 255, 255, 255), anchor="ls")
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    shadow.putalpha(layer.split()[3].filter(ImageFilter.GaussianBlur(max(px // 7, 2))).point(lambda v: int(v * 0.14)))
    Image.alpha_composite(Image.alpha_composite(base, shadow), layer).convert("RGB").save(out)
    return lines


def fit_end_card(card_path, size, out):
    Image, _, _, _, ImageOps = pil()
    ImageOps.fit(Image.open(card_path).convert("RGB"), size, Image.LANCZOS).save(out)
