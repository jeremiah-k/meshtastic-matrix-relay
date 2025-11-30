import asyncio
import os
import re

import PIL.ImageDraw
import s2sphere
import staticmaps
from nio import AsyncClient, UploadResponse
from PIL import Image

from mmrelay.constants.plugins import S2_PRECISION_BITS_TO_METERS_CONSTANT
from mmrelay.log_utils import get_logger
from mmrelay.plugins.base_plugin import BasePlugin


def precision_bits_to_meters(bits: int) -> float | None:
    """
    Convert a precision value in S2 "precision bits" to an approximate radius in meters.

    Parameters:
        bits (int): Precision expressed as S2 precision bits; larger values indicate finer precision.

    Returns:
        float | None: Approximate radius in meters corresponding to `bits`, or `None` if `bits` is less than or equal to 0.
    """
    if bits <= 0:
        return None
    return S2_PRECISION_BITS_TO_METERS_CONSTANT * 0.5**bits


try:
    import cairo  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - optional dependency
    cairo = None


logger = get_logger(__name__)


async def _connect_meshtastic_async() -> object | None:
    """
    Obtain a Meshtastic client connection without blocking the event loop.

    The connector is executed in a thread to avoid blocking the event loop.

    Returns:
        meshtastic_client: The Meshtastic client instance, or `None` if a connection could not be established.
    """
    from mmrelay.meshtastic_utils import connect_meshtastic

    return await asyncio.to_thread(connect_meshtastic)


def textsize(self: PIL.ImageDraw.ImageDraw, *args, **kwargs):
    """
    Compute the width and height of the given text as it would be rendered by this ImageDraw instance.

    Returns:
        (width, height): Tuple containing the text's horizontal and vertical size in pixels.
    """
    left, top, right, bottom = self.textbbox((0, 0), *args, **kwargs)
    return right - left, bottom - top


# Monkeypatch fix for https://github.com/flopp/py-staticmaps/issues/39
PIL.ImageDraw.ImageDraw.textsize = textsize  # type: ignore[attr-defined]


class TextLabel(staticmaps.Object):
    def __init__(self, latlng: s2sphere.LatLng, text: str, fontSize: int = 12) -> None:
        staticmaps.Object.__init__(self)
        self._latlng = latlng
        self._text = text
        self._margin = 4
        self._arrow = 16
        self._font_size = fontSize

    def latlng(self) -> s2sphere.LatLng:
        return self._latlng

    def bounds(self) -> s2sphere.LatLngRect:
        return s2sphere.LatLngRect.from_point(self._latlng)

    def extra_pixel_bounds(self) -> staticmaps.PixelBoundsT:
        # Guess text extents.
        tw = len(self._text) * self._font_size * 0.5
        th = self._font_size * 1.2
        w = max(self._arrow, tw + 2.0 * self._margin)
        return (int(w / 2.0), int(th + 2.0 * self._margin + self._arrow), int(w / 2), 0)

    def render_pillow(self, renderer: staticmaps.PillowRenderer) -> None:
        """
        Render the label as a balloon marker with an arrow and centered text using a Pillow renderer.

        Draws a white balloon with a red outline and black text positioned at the object's latitude/longitude using the provided staticmaps.PillowRenderer.

        Parameters:
            renderer (staticmaps.PillowRenderer): Renderer and drawing context used to convert coordinates to pixels and paint the label.
        """
        x, y = renderer.transformer().ll2pixel(self.latlng())
        x = x + renderer.offset_x()

        # Updated to use textbbox instead of textsize
        bbox = renderer.draw().textbbox((0, 0), self._text)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        w = max(self._arrow, tw + 2 * self._margin)
        h = th + 2 * self._margin

        path = [
            (x, y),
            (x + self._arrow / 2, y - self._arrow),
            (x + w / 2, y - self._arrow),
            (x + w / 2, y - self._arrow - h),
            (x - w / 2, y - self._arrow - h),
            (x - w / 2, y - self._arrow),
            (x - self._arrow / 2, y - self._arrow),
        ]

        renderer.draw().polygon(path, fill=(255, 255, 255, 255))
        renderer.draw().line(path, fill=(255, 0, 0, 255))
        renderer.draw().text(
            (x - tw / 2, y - self._arrow - h / 2 - th / 2),
            self._text,
            fill=(0, 0, 0, 255),
        )

    def render_cairo(self, renderer: staticmaps.CairoRenderer) -> None:
        """
        Render the label as a balloon with a tail and centered text using a Cairo renderer.

        Draws a white rounded balloon with a red outline and black text positioned at this object's latitude/longitude (as provided by latlng()) using the supplied Cairo renderer. If the module-level Cairo binding is unavailable, this method performs no drawing.

        Parameters:
            renderer (staticmaps.CairoRenderer): Cairo renderer used to transform geographic coordinates to pixels and to perform all drawing operations.
        """
        if cairo is None:
            logger.debug("Cairo not available; skipping Cairo label render path")
            return
        x, y = renderer.transformer().ll2pixel(self.latlng())

        ctx = renderer.context()
        ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)

        ctx.set_font_size(self._font_size)
        x_bearing, y_bearing, tw, th, _, _ = ctx.text_extents(self._text)

        w = max(self._arrow, tw + 2 * self._margin)
        h = th + 2 * self._margin

        path = [
            (x, y),
            (x + self._arrow / 2, y - self._arrow),
            (x + w / 2, y - self._arrow),
            (x + w / 2, y - self._arrow - h),
            (x - w / 2, y - self._arrow - h),
            (x - w / 2, y - self._arrow),
            (x - self._arrow / 2, y - self._arrow),
        ]

        ctx.set_source_rgb(1, 1, 1)
        ctx.new_path()
        for p in path:
            ctx.line_to(*p)
        ctx.close_path()
        ctx.fill()

        ctx.set_source_rgb(1, 0, 0)
        ctx.set_line_width(1)
        ctx.new_path()
        for p in path:
            ctx.line_to(*p)
        ctx.close_path()
        ctx.stroke()

        ctx.set_source_rgb(0, 0, 0)
        ctx.set_line_width(1)
        ctx.move_to(
            x - tw / 2 - x_bearing, y - self._arrow - h / 2 - y_bearing - th / 2
        )
        ctx.show_text(self._text)
        ctx.stroke()

    def render_svg(self, renderer: staticmaps.SvgRenderer) -> None:
        """
        Render this label as an SVG balloon with centered text at the object's latitude/longitude pixel position.

        Parameters:
            renderer (staticmaps.SvgRenderer): SVG renderer used to transform geographic coordinates to pixels and to emit SVG elements. The method adds a filled rounded balloon path and a centered text element to the renderer's current group.
        """
        x, y = renderer.transformer().ll2pixel(self.latlng())

        # guess text extents
        tw = len(self._text) * self._font_size * 0.5
        th = self._font_size * 1.2

        w = max(self._arrow, tw + 2 * self._margin)
        h = th + 2 * self._margin

        path = renderer.drawing().path(
            fill="#ffffff",
            stroke="#ff0000",
            stroke_width=1,
            opacity=1.0,
        )
        path.push(f"M {x} {y}")
        path.push(f" l {self._arrow / 2} {-self._arrow}")
        path.push(f" l {w / 2 - self._arrow / 2} 0")
        path.push(f" l 0 {-h}")
        path.push(f" l {-w} 0")
        path.push(f" l 0 {h}")
        path.push(f" l {w / 2 - self._arrow / 2} 0")
        path.push("Z")
        renderer.group().add(path)

        renderer.group().add(
            renderer.drawing().text(
                self._text,
                text_anchor="middle",
                dominant_baseline="central",
                insert=(x, y - self._arrow - h / 2),
                font_family="sans-serif",
                font_size=f"{self._font_size}px",
                fill="#000000",
            )
        )


def anonymize_location(
    lat: float,
    lon: float,
    _radius: float = 1000,  # deprecated; kept for compat
) -> tuple[float, float]:
    """
    Return the input latitude and longitude unchanged.

    Parameters:
        lat (float): Latitude in decimal degrees.
        lon (float): Longitude in decimal degrees.
        _radius (float): Deprecated and ignored; kept for backward compatibility.

    Returns:
        tuple[float, float]: The same (lat, lon) values passed in.
    """
    return lat, lon


def get_map(
    locations: list[dict],
    zoom: int | None = None,
    image_size: tuple[int, int] | None = None,
    anonymize: bool = False,  # noqa: ARG001
    radius: int = 10000,  # noqa: ARG001
) -> Image.Image:
    """
    Generate a static map image with labeled location markers.

    Renders a map containing each entry in `locations` as a labeled marker; coordinates are used as provided. If
    a location includes ``precisionBits``, a lightly shaded circle representing that precision radius is drawn.

    Parameters:
        locations (Iterable[dict]): Iterable of dicts with keys "lat", "lon", and "label". Optional "precisionBits" controls shaded radius.
        zoom (int | None): Map zoom level to use. If None the Context's default zoom applies.
        image_size (tuple[int, int] | None): (width, height) in pixels for the output image. If None, defaults to (1000, 1000). Dimensions are clamped by caller logic.
        anonymize (bool): Deprecated; ignored (coordinates are not altered).
        radius (int): Deprecated; ignored (coordinates are not altered).

    Returns:
        PIL.Image.Image: A Pillow image containing the rendered map with labels.
    """
    context = staticmaps.Context()
    context.set_tile_provider(staticmaps.tile_provider_OSM)
    context.set_zoom(zoom)

    circle_cls = getattr(staticmaps, "Circle", None)
    color_cls = getattr(staticmaps, "Color", None)

    for location in locations:
        radio = staticmaps.create_latlng(float(location["lat"]), float(location["lon"]))
        precision_bits = location.get("precisionBits")
        precision_radius_m = None
        if precision_bits is not None:
            try:
                precision_radius_m = precision_bits_to_meters(int(precision_bits))
            except (TypeError, ValueError):
                precision_radius_m = None
        if precision_radius_m is not None and circle_cls and color_cls:
            context.add_object(
                circle_cls(
                    radio,
                    precision_radius_m,
                    fill_color=color_cls(0, 0, 0, 48),
                    color=color_cls(0, 0, 0, 64),
                )
            )
        context.add_object(TextLabel(radio, location["label"], fontSize=50))

    # render non-anti-aliased png
    if image_size:
        return context.render_pillow(image_size[0], image_size[1])
    else:
        return context.render_pillow(1000, 1000)


class Plugin(BasePlugin):
    """Static map generation plugin for mesh node locations.

    Generates static maps showing positions of mesh nodes with labeled markers.
    Supports customizable zoom levels, image sizes, and renders firmware-provided precision as shaded circles.

    Commands:
        !map: Generate map with default settings
        !map zoom=N: Set zoom level (0-30)
        !map size=W,H: Set image dimensions (max 1000x1000)

    Configuration:
        zoom (int): Default zoom level (default: 8)
        image_width/image_height (int): Default image size (default: 1000x1000)
        anonymize (bool): Deprecated; coordinates are not altered by this plugin.
        radius (int): Deprecated; retained for backward compatibility.

    Uploads generated maps as images to Matrix rooms.
    """

    is_core_plugin = True
    plugin_name = "map"

    def __init__(self):
        super().__init__()

    @property
    def description(self):
        return (
            "Map of mesh radio nodes. Supports `zoom` and `size` options to customize"
        )

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ) -> bool:
        return False

    def get_matrix_commands(self):
        return [self.plugin_name]

    def get_mesh_commands(self):
        return []

    async def handle_room_message(self, room, event, full_message) -> bool:
        # Pass the whole event to matches() for compatibility w/ updated base_plugin.py
        """
        Handle "!map" commands in a Matrix room by generating a static map of known mesh node locations and sending it to the room.

        Parses optional parameters in the incoming message for zoom (zoom=N) and image size (size=W,H). Collects node positions from the Meshtastic client, renders firmware-provided precision as shaded circles, builds a map image, and uploads it to the room as "location.png".

        Parameters:
            room: The Matrix room object where the message was received; used to determine the destination room ID.
            event: The full Matrix event object passed to matches(); used for plugin matching.
            full_message (str): The raw message text to parse for the "!map" command and optional parameters.

        Returns:
            bool: `True` if the message was recognized, a map was generated, and the image was sent; `False` if the message did not target this plugin or was not processed.
        """
        if not self.matches(event):
            return False

        args = self.extract_command_args("map", full_message)
        if args is None:
            return False

        # Accept zoom/size in any order, but reject unknown tokens
        token_pattern = r"(?:\s*(?:zoom=\d+|size=\d+,\d+))*\s*$"
        if args and not re.fullmatch(token_pattern, args, flags=re.IGNORECASE):
            return False

        zoom_match = re.search(r"zoom=(\d+)", args, flags=re.IGNORECASE)
        size_match = re.search(r"size=(\d+),\s*(\d+)", args, flags=re.IGNORECASE)

        zoom = zoom_match.group(1) if zoom_match else None
        image_size = size_match.groups() if size_match else (None, None)

        try:
            zoom = int(zoom)
        except (TypeError, ValueError):
            try:
                zoom = int(self.config.get("zoom", 8))
            except (TypeError, ValueError):
                zoom = 8

        if not 0 <= zoom <= 30:
            zoom = 8

        try:
            image_size = (int(image_size[0]), int(image_size[1]))
        except (TypeError, ValueError):
            width, height = 1000, 1000
            try:
                width = int(self.config.get("image_width", 1000))
            except (TypeError, ValueError):
                pass  # keep default
            try:
                height = int(self.config.get("image_height", 1000))
            except (TypeError, ValueError):
                pass  # keep default
            image_size = (width, height)

        width = max(1, min(image_size[0], 1000))
        height = max(1, min(image_size[1], 1000))
        image_size = (width, height)

        from mmrelay.matrix_utils import (
            ImageUploadError,
            connect_matrix,
            send_image,
        )

        matrix_client = await connect_matrix()
        if matrix_client is None:
            logger.error("Failed to connect to Matrix client; cannot generate map")
            await self.send_matrix_message(
                room.room_id,
                "Cannot generate map: Matrix client unavailable.",
                formatted=False,
            )
            return True
        meshtastic_client = await _connect_meshtastic_async()

        has_nodes = getattr(meshtastic_client, "nodes", None) is not None

        if not meshtastic_client or not has_nodes:
            self.logger.error("Meshtastic client unavailable; cannot generate map")
            await self.send_matrix_message(
                room.room_id,
                "Cannot generate map: Meshtastic client unavailable.",
                formatted=False,
            )
            return True

        locations = []
        for _node, info in meshtastic_client.nodes.items():
            if "position" in info and "latitude" in info["position"]:
                locations.append(
                    {
                        "lat": info["position"]["latitude"],
                        "lon": info["position"]["longitude"],
                        "precisionBits": info["position"].get("precisionBits"),
                        "label": info["user"]["shortName"],
                    }
                )

        if not locations:
            await self.send_matrix_message(
                room.room_id,
                "Cannot generate map: No nodes with location data found.",
                formatted=False,
            )
            return True

        # Offload CPU-bound rendering to keep the event loop responsive.
        pillow_image = await asyncio.to_thread(
            get_map,
            locations=locations,
            zoom=zoom,
            image_size=image_size,
            anonymize=False,
            radius=0,
        )

        try:
            await send_image(matrix_client, room.room_id, pillow_image, "location.png")
        except ImageUploadError:
            self.logger.exception("Failed to send map image")
            await matrix_client.room_send(
                room_id=room.room_id,
                message_type="m.room.message",
                content={
                    "msgtype": "m.notice",
                    "body": "Failed to generate map: Image upload failed.",
                },
            )
            return False

        return True
