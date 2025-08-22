from __future__ import annotations  # noqa: D100,I001
import subprocess
from io import BytesIO, StringIO
from pathlib import Path
from typing import Literal, TypedDict

import yaml
from wand.image import Image
from wand.drawing import Drawing
from wand.color import Color

# Define all possible alignments (classic 3x3 chart)
AlignmentName = Literal[
        "Lawful Good",
        "Neutral Good",
        "Chaotic Good",
        "Lawful Neutral",
        "True Neutral",
        "Chaotic Neutral",
        "Lawful Evil",
        "Neutral Evil",
        "Chaotic Evil",
]


BorderShape = Literal["square", "circle", "rounded"]


# User entry in YAML
class UserAlignment(TypedDict):
        alignment: AlignmentName
        display_name: str
        avatar_url: str | None


class AlignmentChart(TypedDict):
        users: dict[str, UserAlignment]
        admins: list[str]  # user IDs as strings


def solid_color_background(
        width: int = 512,
        height: int = 512,
        color: str = "black",
) -> Image:
        """Return a solid color background."""
        return Image(width=width, height=height, background=Color(color))


def debug_solid_color_background() -> None:
        output_path = Path("debug_bg.png")
        bg = solid_color_background(300, 200, "blue")
        bg.save(filename=str(output_path))
        print(f"Saved {output_path} — opening in Gwenview...")
        subprocess.run(["gwenview", str(output_path)], check=False)  # noqa: S603, S607
        output_path.unlink()


def debug_styled_profile() -> None:
        output_path = Path("debug_bg.png")
        bg = solid_color_background(300, 200, "blue")
        bg_styled = process_avatar(
                bg,
                (300, 200),
                30,
                "green",
                "rounded",
        )
        bg_styled.save(filename=str(output_path))
        print(f"Saved {output_path} — opening in Gwenview...")
        subprocess.run(["gwenview", str(output_path)], check=False)  # noqa: S603, S607
        output_path.unlink()


def layout_positions(
        num_images: int,
        cell_size: tuple[int, int],
        gap: int,
) -> list[tuple[int, int]]:
        """Calculate top-left positions for a simple grid layout."""
        cols = int((cell_size[0] + gap) // (cell_size[0] + gap))
        print(cols)
        # For now we can just return fixed offsets for all images in a row
        # Later we can make a proper auto-grid based on aspect ratio
        return [(0, i * (cell_size[1] + gap)) for i in range(num_images)]


def process_avatar(
        img: Image,
        size: tuple[int, int],
        border: int,
        border_color: str,
        shape: BorderShape,
) -> Image:
        img.resize(size[0], size[1])

        # Apply shape
        match shape:
                case "circle":
                        with (
                                Drawing() as mask,
                                Image(
                                        width=size[0],
                                        height=size[1],
                                        background=Color("transparent"),
                                ) as mask_img,
                        ):
                                mask.circle(
                                        (size[0] // 2, size[1] // 2),
                                        (size[0] // 2, size[1]),
                                )
                                mask.draw(mask_img)
                                img.composite_channel(
                                        "alpha",
                                        mask_img,
                                        "copy_alpha",
                                        0,
                                        0,
                                )
                case "rounded":
                        with (
                                Drawing() as mask,
                                Image(
                                        width=size[0],
                                        height=size[1],
                                        background=Color("transparent"),
                                ) as mask_img,
                        ):
                                radius = min(size) // 8
                                mask.rectangle(
                                        0,
                                        0,
                                        size[0],
                                        size[1],
                                        radius=radius,
                                )

                                mask.draw(mask_img)
                                img.composite_channel(
                                        "alpha",
                                        mask_img,
                                        "copy_alpha",
                                        0,
                                        0,
                                )
                case _:
                        pass
        # Add border
        if border > 0:
                img.border(Color(border_color), border, border)

        return img.clone()


def normalize_chart(chart: dict) -> AlignmentChart:
        return {
                "users": chart.get("users", {}),
                "admins": chart.get("admins", []),
        }


def load_alignment_chart(buf: BytesIO) -> AlignmentChart:
        buf.seek(0)
        chart = yaml.safe_load(buf) or {}
        return normalize_chart(chart)


def parse_alignment_chart(data: BytesIO) -> AlignmentChart:
        data.seek(0)
        text = data.read().decode("utf-8") or ""
        loaded = yaml.safe_load(text)
        if not loaded:
                return {"users": {}, "admins": []}
        if "users" not in loaded:
                loaded["users"] = {}
        if "admins" not in loaded:
                loaded["admins"] = []
        return normalize_chart(loaded)  # type: ignore


def serialize_alignment_chart(chart: AlignmentChart) -> BytesIO:
        """Convert Python dict into YAML BytesIO."""
        text_buf = StringIO()
        yaml.safe_dump(chart, text_buf, sort_keys=False)
        bytes_buf = BytesIO(text_buf.getvalue().encode("utf-8"))
        bytes_buf.seek(0)
        return bytes_buf


def set_user_alignment(
        chart: AlignmentChart,
        user_id: str,
        alignment: AlignmentName,
        display_name: str,
        avatar_url: str | None = None,
) -> AlignmentChart:
        """Return a new chart with updated alignment for a user."""
        new_chart = {"users": dict(chart["users"])}
        new_chart["users"][user_id] = {
                "alignment": alignment,
                "display_name": display_name,
                "avatar_url": avatar_url,
        }
        return new_chart  # type: ignore


def remove_user_alignment(
        chart: AlignmentChart,
        user_id: str,
) -> AlignmentChart:
        """Return a new chart with a user removed."""
        new_chart = {"users": dict(chart["users"])}
        new_chart["users"].pop(user_id, None)
        return new_chart  # type: ignore


def load_file_buffer(path: Path) -> BytesIO:
        """Load YAML file from disk into BytesIO. Creates empty if missing."""
        if not path.exists():
                return BytesIO()
        return BytesIO(path.read_bytes())


def save_file_buffer(path: Path, data: BytesIO) -> None:
        """Write YAML BytesIO back to disk."""
        path.write_bytes(data.getvalue())


if __name__ == "__main__":
        file_path = Path("server_12345.yaml")

        # Load from disk
        raw_data = load_file_buffer(file_path)

        # Parse
        chart = parse_alignment_chart(raw_data)

        # Modify (pure)
        updated_chart = set_user_alignment(
                chart,
                user_id="111222333",
                alignment="Chaotic Good",
                display_name="EpicUser",
                avatar_url=(
                        "https://cdn.discordapp.com"
                        "/avatars/111222333/avatar.png"
                ),
        )
        print(updated_chart, chart, raw_data)
        # Serialize (pure)
        new_bytes = serialize_alignment_chart(updated_chart)

        # Save (impure)
        save_file_buffer(file_path, new_bytes)
        debug_solid_color_background()
        debug_styled_profile()
