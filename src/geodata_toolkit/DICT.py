"""Helpers for querying a configured GRDF API and exporting an area image."""

from __future__ import annotations

import json
import math
import os
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _read_json(url: str, headers: dict[str, str] | None = None):
	request = Request(url, headers=headers or {"Accept": "application/json"})
	with urlopen(request, timeout=30) as response:
		return json.loads(response.read().decode("utf-8"))


def fetch_grdf_data(
	endpoint: str = "",
	*,
	params: dict | None = None,
	base_url: str | None = None,
	api_token: str | None = None,
):
	"""Fetch JSON from a GRDF API endpoint.

	Set ``GRDF_API_BASE_URL`` to the API's base URL and, if required,
	``GRDF_API_TOKEN`` to its bearer token. Endpoint and parameters depend on
	the GRDF API product and the access granted to the caller.
	"""
	base_url = (base_url or os.getenv("GRDF_API_BASE_URL", "")).rstrip("/")
	if not base_url:
		raise ValueError("Set GRDF_API_BASE_URL to the GRDF API base URL.")
	url = f"{base_url}/{endpoint.lstrip('/')}" if endpoint else base_url
	if params:
		url += ("&" if "?" in url else "?") + urlencode(params, doseq=True)
	token = api_token or os.getenv("GRDF_API_TOKEN")
	headers = {"Accept": "application/json"}
	if token:
		headers["Authorization"] = f"Bearer {token}"
	return _read_json(url, headers)


def _coordinates_from_address(address: str) -> tuple[float, float]:
	"""Geocode an address with the French BAN service; returns lat, lon."""
	url = "https://api-adresse.data.gouv.fr/search/?" + urlencode(
		{"q": address, "limit": 1}
	)
	result = _read_json(url)
	features = result.get("features", [])
	if not features:
		raise ValueError(f"Address not found: {address}")
	lon, lat = features[0]["geometry"]["coordinates"]
	return float(lat), float(lon)


def export_area_image(
	output_path: str | Path,
	*,
	address: str | None = None,
	latitude: float | None = None,
	longitude: float | None = None,
	radius_m: int = 500,
	zoom: int = 17,
) -> Path:
	"""Save an aerial image centered on an address or latitude/longitude.

	``radius_m`` is the approximate half-width of the square image. Imagery is
	fetched from Esri World Imagery. Install Pillow to use this function.
	"""
	if address:
		if latitude is not None or longitude is not None:
			raise ValueError("Provide an address or coordinates, not both.")
		latitude, longitude = _coordinates_from_address(address)
	elif latitude is None or longitude is None:
		raise ValueError("Provide an address or both latitude and longitude.")

	latitude, longitude = float(latitude), float(longitude)
	if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
		raise ValueError("Coordinates are outside valid latitude/longitude ranges.")
	if radius_m <= 0 or not 1 <= zoom <= 19:
		raise ValueError("radius_m must be positive and zoom must be between 1 and 19.")

	try:
		from PIL import Image, ImageDraw
	except ImportError as exc:
		raise ImportError("Install Pillow to export images: pip install Pillow") from exc

	tile_size = 256
	world_pixels = tile_size * (2**zoom)
	clamped_lat = max(-85.05112878, min(85.05112878, latitude))
	lat_rad = math.radians(clamped_lat)
	center_x = (longitude + 180) / 360 * world_pixels
	center_y = (1 - math.asinh(math.tan(lat_rad)) / math.pi) / 2 * world_pixels
	meters_per_pixel = 156543.03392 * math.cos(lat_rad) / (2**zoom)
	half_size = max(1, math.ceil(radius_m / meters_per_pixel))
	left, top = math.floor(center_x - half_size), math.floor(center_y - half_size)
	right, bottom = math.ceil(center_x + half_size), math.ceil(center_y + half_size)

	min_tx, max_tx = left // tile_size, (right - 1) // tile_size
	min_ty, max_ty = top // tile_size, (bottom - 1) // tile_size
	mosaic = Image.new(
		"RGB", ((max_tx - min_tx + 1) * tile_size, (max_ty - min_ty + 1) * tile_size)
	)
	for tile_x in range(min_tx, max_tx + 1):
		for tile_y in range(min_ty, max_ty + 1):
			if not 0 <= tile_y < 2**zoom:
				continue
			wrapped_x = tile_x % (2**zoom)
			tile_url = (
				"https://server.arcgisonline.com/ArcGIS/rest/services/"
				f"World_Imagery/MapServer/tile/{zoom}/{tile_y}/{wrapped_x}"
			)
			request = Request(tile_url, headers={"User-Agent": "geodata-toolkit/1.0"})
			with urlopen(request, timeout=30) as response:
				tile = Image.open(BytesIO(response.read())).convert("RGB")
			mosaic.paste(
				tile,
				((tile_x - min_tx) * tile_size, (tile_y - min_ty) * tile_size),
			)

	crop_left, crop_top = left - min_tx * tile_size, top - min_ty * tile_size
	image = mosaic.crop(
		(crop_left, crop_top, crop_left + right - left, crop_top + bottom - top)
	)
	draw = ImageDraw.Draw(image)
	draw.rectangle((0, image.height - 20, image.width, image.height), fill="white")
	draw.text((5, image.height - 17), "Imagery: Esri", fill="black")

	destination = Path(output_path)
	destination.parent.mkdir(parents=True, exist_ok=True)
	image.save(destination)
	return destination