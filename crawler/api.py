from typing import NamedTuple
from urllib.parse import quote

import requests



class LatLong(NamedTuple):
    lat: float
    lng: float


class PanoId:
    def __init__(self, coords: LatLong, api_key: str, session: requests.Session):
        self._key: str = api_key
        self._session: requests.Session = session
        self._coords: LatLong = coords

    def as_str(self) -> str:
        location = quote(f"{self._coords.lat},{self._coords.lng}")
        resp = self._session.get(
            f"https://maps.googleapis.com/maps/api/streetview/metadata?location={location}&key={self._key}"
        )
        return resp.json()["pano_id"]

    def location(self) -> LatLong:
        return self._coords
