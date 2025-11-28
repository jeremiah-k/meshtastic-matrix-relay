import io
import math
import random
import re

import PIL.ImageDraw
import s2sphere
import staticmaps
from nio import AsyncClient, UploadResponse
from PIL import Image

from mmrelay.plugins.base_plugin import BasePlugin


def textsize(self: PIL.ImageDraw.ImageDraw, *args, **kwargs):
    x, y, w, h = self.textbbox((0, 0), *args, **kwargs)
    return w, h


# Monkeypatch fix for https://github.com/flopp/py-staticmaps/issues/39
PIL.ImageDraw.ImageDraw.textsize = textsize


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


def anonymize_location(lat, lon, radius=1000):
    """Add random offset to GPS coordinates for privacy protection.

    Args:
        lat (float): Original latitude
        lon (float): Original longitude
        radius (int): Maximum offset distance in meters (default: 1000)

    Returns:
        tuple: (new_lat, new_lon) with random offset applied

    Adds random offset within specified radius to obscure exact locations
    while maintaining general geographic area for mapping purposes.
    """
    # Generate random offsets for latitude and longitude
    lat_offset = random.uniform(-radius / 111320, radius / 111320)
    lon_offset = random.uniform(
        -radius / (111320 * math.cos(lat)), radius / (111320 * math.cos(lat))
    )

    # Apply the offsets to the location coordinates
    new_lat = lat + lat_offset
    new_lon = lon + lon_offset

    return new_lat, new_lon


def get_map(locations, zoom=None, image_size=None, anonymize=True, radius=10000):
    """
    Generate a static map image with labeled location markers.
    
    Renders a map containing each entry in `locations` as a labeled marker; coordinates may be randomly offset for privacy.
    
    Parameters:
        locations (Iterable[dict]): Iterable of dicts with keys "lat", "lon", and "label". "lat" and "lon" are numeric (or numeric strings) representing latitude and longitude in degrees; "label" is the text shown for the marker.
        zoom (int | None): Map zoom level to use. If None the Context's default zoom applies.
        image_size (tuple[int, int] | None): (width, height) in pixels for the output image. If None, defaults to (1000, 1000). Dimensions are clamped by caller logic.
        anonymize (bool): If True, apply a random offset to each coordinate to preserve privacy.
        radius (int): Maximum anonymization offset in meters applied when `anonymize` is True.
    
    Returns:
        PIL.Image.Image: A Pillow image containing the rendered map with labels.
    """
    context = staticmaps.Context()
    context.set_tile_provider(staticmaps.tile_provider_OSM)
    context.set_zoom(zoom)

    for location in locations:
        if anonymize:
            new_location = anonymize_location(
                lat=float(location["lat"]),
                lon=float(location["lon"]),
                radius=radius,
            )
            radio = staticmaps.create_latlng(new_location[0], new_location[1])
        else:
            radio = staticmaps.create_latlng(
                float(location["lat"]), float(location["lon"])
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
    Supports customizable zoom levels, image sizes, and privacy features.

    Commands:
        !map: Generate map with default settings
        !map zoom=N: Set zoom level (0-30)
        !map size=W,H: Set image dimensions (max 1000x1000)

    Configuration:
        zoom (int): Default zoom level (default: 8)
        image_width/image_height (int): Default image size (default: 1000x1000)
        anonymize (bool): Whether to offset coordinates for privacy (default: true)
        radius (int): Anonymization offset radius in meters (default: 1000)

    Uploads generated maps as images to Matrix rooms.
    """

    is_core_plugin = True
    plugin_name = "map"

    # No __init__ method needed with the simplified plugin system
    # The BasePlugin will automatically use the class-level plugin_name

    @property
    def description(self):
        return (
            "Map of mesh radio nodes. Supports `zoom` and `size` options to customize"
        )

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ):
        return False

    def get_matrix_commands(self):
        return [self.plugin_name]

    def get_mesh_commands(self):
        return []

    async def handle_room_message(self, room, event, text):
        # Pass the whole event to matches() for compatibility w/ updated base_plugin.py
        """
        Handle "!map" commands in a Matrix room by generating a static map of known mesh node locations and sending it to the room.
        
        Parses optional parameters in the incoming message for zoom (zoom=N) and image size (size=W,H). Collects node positions from the Meshtastic client, optionally anonymizes coordinates per plugin configuration, builds a map image, and uploads it to the room as "location.png".
        
        Parameters:
            room: The Matrix room object where the message was received; used to determine the destination room ID.
            event: The full Matrix event object passed to matches(); used for plugin matching.
            text (str): The raw message text to parse for the "!map" command and optional parameters.
        
        Returns:
            bool: `True` if the message was recognized, a map was generated, and the image was sent; `False` if the message did not target this plugin or was not processed.
        """
        if not self.matches(event):
            return False

        from mmrelay.matrix_utils import (
            ImageUploadError,
            connect_matrix,
            send_image,
        )
        from mmrelay.meshtastic_utils import connect_meshtastic

        matrix_client = await connect_matrix()
        meshtastic_client = connect_meshtastic()

        pattern = r"^(?:.+?:\s*)?!map(?:\s+zoom=(\d+))?(?:\s+size=(\d+),(\d+))?$"
        match = re.match(pattern, text)

        # Indicate this message is not meant for this plugin
        if not match:
            return False

        zoom = match.group(1)
        image_size = match.group(2, 3)

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

        if image_size[0] > 1000 or image_size[1] > 1000:
            image_size = (1000, 1000)

        locations = []
        for _node, info in meshtastic_client.nodes.items():
            if "position" in info and "latitude" in info["position"]:
                locations.append(
                    {
                        "lat": info["position"]["latitude"],
                        "lon": info["position"]["longitude"],
                        "label": info["user"]["shortName"],
                    }
                )

        anonymize = self.config.get("anonymize", True)
        radius = self.config.get("radius", 1000)

        pillow_image = get_map(
            locations=locations,
            zoom=zoom,
            image_size=image_size,
            anonymize=anonymize,
            radius=radius,
        )

        try:
            await send_image(matrix_client, room.room_id, pillow_image, "location.png")
        except ImageUploadError as exc:
            self.logger.error(f"Failed to send map image: {exc}")
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
