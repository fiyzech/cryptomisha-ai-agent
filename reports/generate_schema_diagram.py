from PIL import Image, ImageDraw, ImageFont
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "crypto_pulse_schema.png"

W, H = 1800, 1120
BG = "#171717"
GRID = "#202020"
CARD = "#111111"
BORDER = "#2f2f2f"
TEXT = "#f4f4f4"
MUTED = "#a1a1aa"
KEY = "#d4d4d8"
LINE = "#8b8b8b"


def load_font(size, bold=False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT_TITLE = load_font(24, bold=True)
FONT_ROW = load_font(18)
FONT_TYPE = load_font(16)


tables = {
    "users": {
        "x": 630, "y": 40, "w": 430,
        "rows": [
            ("email", "varchar", ""),
            ("username", "varchar", ""),
            ("subscription", "varchar", ""),
            ("avatar_url", "varchar", ""),
            ("first_name", "varchar", ""),
            ("last_name", "varchar", ""),
            ("phone_number", "varchar", ""),
            ("birth_date", "text", ""),
            ("region", "varchar", ""),
            ("active_plan", "varchar", ""),
            ("id", "uuid", "pk"),
            ("billing_cycle", "varchar", ""),
        ],
    },
    "alerts": {
        "x": 1260, "y": 170, "w": 360,
        "rows": [
            ("id", "uuid", "pk"),
            ("user_id", "uuid", "fk"),
            ("symbol", "text", ""),
            ("condition", "text", ""),
            ("target_price", "numeric", ""),
            ("is_active", "bool", ""),
        ],
    },
    "model_predictions": {
        "x": 60, "y": 600, "w": 390,
        "rows": [
            ("id", "int4", "pk"),
            ("symbol", "varchar", ""),
            ("interval", "varchar", ""),
            ("signal", "varchar", ""),
            ("price", "numeric", ""),
            ("confidence", "numeric", ""),
            ("accuracy", "numeric", ""),
            ("raw_prediction", "varchar", ""),
            ("stop_loss", "numeric", ""),
            ("take_profit", "numeric", ""),
            ("created_at", "timestamptz", ""),
        ],
    },
    "user_favorites": {
        "x": 630, "y": 720, "w": 390,
        "rows": [
            ("id", "int8", "pk"),
            ("user_id", "uuid", "fk"),
            ("coin_id", "text", ""),
            ("symbol", "varchar", ""),
            ("name", "text", ""),
            ("image_url", "text", ""),
            ("created_at", "timestamptz", ""),
        ],
    },
    "ml_models": {
        "x": 1260, "y": 720, "w": 390,
        "rows": [
            ("id", "int4", "pk"),
            ("symbol", "varchar", ""),
            ("interval", "varchar", ""),
            ("model_binary", "bytea", ""),
            ("accuracy", "real", ""),
            ("features", "array", ""),
            ("trained_at", "timestamptz", ""),
        ],
    },
}


def box_height(rows):
    return 58 + len(rows) * 38


def draw_grid(draw):
    for x in range(0, W, 36):
        for y in range(0, H, 36):
            draw.ellipse((x, y, x + 2, y + 2), fill=GRID)


def draw_table(draw, name, spec):
    x, y, w = spec["x"], spec["y"], spec["w"]
    h = box_height(spec["rows"])
    draw.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=CARD, outline=BORDER, width=2)
    draw.rectangle((x, y, x + w, y + 56), fill="#151515")
    draw.line((x, y + 56, x + w, y + 56), fill=BORDER, width=2)
    draw.text((x + 18, y + 16), name, font=FONT_TITLE, fill=TEXT)

    row_y = y + 70
    for field, field_type, marker in spec["rows"]:
        if marker == "pk":
            draw.text((x + 18, row_y), "◆", font=FONT_ROW, fill=KEY)
        elif marker == "fk":
            draw.text((x + 18, row_y), "◇", font=FONT_ROW, fill=KEY)
        else:
            draw.text((x + 18, row_y), "◇", font=FONT_ROW, fill="#6b7280")
        draw.text((x + 48, row_y), field, font=FONT_ROW, fill=TEXT)
        bbox = draw.textbbox((0, 0), field_type, font=FONT_TYPE)
        tw = bbox[2] - bbox[0]
        draw.text((x + w - tw - 18, row_y + 2), field_type, font=FONT_TYPE, fill=MUTED)
        row_y += 38
    return (x, y, x + w, y + h)


def mid_right(box):
    return (box[2], (box[1] + box[3]) // 2)


def mid_left(box):
    return (box[0], (box[1] + box[3]) // 2)


def bottom_mid(box):
    return ((box[0] + box[2]) // 2, box[3])


def top_mid(box):
    return ((box[0] + box[2]) // 2, box[1])


def elbow(draw, points):
    draw.line(points, fill=LINE, width=3)


def main():
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw_grid(draw)

    boxes = {name: draw_table(draw, name, spec) for name, spec in tables.items()}

    # users -> alerts
    a = mid_right(boxes["users"])
    b = mid_left(boxes["alerts"])
    elbow(draw, [a, (1135, a[1]), (1135, b[1]), b])

    # users -> user_favorites
    a = bottom_mid(boxes["users"])
    b = top_mid(boxes["user_favorites"])
    elbow(draw, [a, (a[0], 650), (b[0], 650), b])

    draw.text((48, 18), "Crypto Pulse database schema", font=FONT_TITLE, fill=TEXT)
    image.save(OUT)


if __name__ == "__main__":
    main()
