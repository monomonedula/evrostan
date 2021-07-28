import os
import sys
import io
import operator
import csv
from abc import ABC, abstractmethod
from pathlib import Path
from typing import NamedTuple, Callable, Optional, List, Tuple
from urllib.parse import quote

import click
from PIL import Image
from methodtools import lru_cache
from loguru import logger
import requests
import geopy.distance
from geopy import Point


class LatLong(NamedTuple):
    lat: float
    lng: float


class PanoIdOf:
    def __init__(self, coords: Point, api_key: str, session: requests.Session):
        self._key: str = api_key
        self._session: requests.Session = session
        self._coords: Point = coords

    def as_str(self) -> Optional[str]:
        data = self._resp()
        if data["status"] == "ZERO_RESULTS":
            return None
        if data["status"] == "OK":
            return data["pano_id"]
        return None

    def pano_location(self) -> Optional[Point]:
        data = self._resp()
        if data["status"] == "ZERO_RESULTS":
            return None
        if data["status"] == "OK":
            return Point(data["location"]["lat"], data["location"]["lng"])
        return None

    @lru_cache()
    def _resp(self):
        location = quote(f"{self._coords.latitude},{self._coords.longitude}")
        return self._session.get(
            f"https://maps.googleapis.com/maps/api/streetview/metadata?location={location}&key={self._key}"
        ).json()

    def location(self) -> Point:
        return self._coords


class PointsInSquare:
    def __init__(self, centre: Point, square_side: int = 1000, step: int = 20):
        self._centre: Point = centre
        self._side: int = square_side
        self._step: int = step

    def iter(self):
        start = self.upper_left_corner()
        for i in range(0, self._side + 1, 30):
            for j in range(0, self._side + 1, 30):
                yield geopy.distance.distance(meters=j).destination(
                    point=geopy.distance.distance(meters=i).destination(point=start, bearing=90),
                    bearing=180
                )

    def upper_left_corner(self) -> Point:
        left = geopy.distance.distance(meters=self._side / 2).destination(point=self._centre, bearing=270)
        upper_left = geopy.distance.distance(meters=self._side / 2).destination(point=left, bearing=0)
        return upper_left


class ImgRequest(NamedTuple):
    url: str
    fov: int
    heading: int


class Pano:
    def __init__(self, pano_id: str, location: Point, api_key: str):
        self._id: str = pano_id
        self._location: Point = location
        self._api_key: str = api_key

    def id(self) -> str:
        return self._id

    def location(self) -> Point:
        return self._location

    def image_requests(self, fov: int = 90, width: int = 600, height: int = 400) -> List[ImgRequest]:
        assert 360 % 90 == 0
        return [
            ImgRequest(
                url=f"https://maps.googleapis.com/maps/api/streetview?size={width}x{height}"
                f"&pano={self._id}&heading={heading}&fov={fov}&key={self._api_key}&return_error_code=true",
                fov=fov,
                heading=heading,
            )
            for heading in range(0, 360, fov)
        ]


class Panos:
    def __init__(self, pts: PointsInSquare, pano_id: Callable[[Point], PanoIdOf], pano: Callable[[str, Point], Pano]):
        self._pts: PointsInSquare = pts
        self._pano_id: Callable[[Point], PanoIdOf] = pano_id
        self._pano: Callable[[str, Point], Pano] = pano

    def as_list(self) -> List[Pano]:
        # todo: filter panos that are too close to each other
        ids = dict()
        for p in self._pts.iter():
            logger.info(f"Getting pano id for {p.latitude},{p.longitude}...")
            pano_id = self._pano_id(p)
            id_str = pano_id.as_str()
            location = pano_id.location()
            if id_str is not None:
                assert location is not None
                ids[id_str] = location
            else:
                logger.info(f"Got no pano id for {p.latitude},{p.longitude}.")
        return [self._pano(id_, location) for id_, location in sorted(ids.items(), key=operator.itemgetter(0))]


def parsed_centre(p: str) -> Point:
    lat, lng = p.split(",")
    return Point(float(lat), float(lng))


class PanoFolder(ABC):
    @abstractmethod
    def save(self, pics: List[Tuple[bytes, ImgRequest]]) -> List[Path]:
        pass


class PanoFolderSimple(PanoFolder):
    def __init__(self, directory: Path, pano_id: str):
        self._dir: Path = directory
        self._pano: str = pano_id

    def save(self, pics: List[Tuple[bytes, ImgRequest]]) -> List[Path]:
        return [
            self.save_one(pic, rq)
            for pic, rq in pics
        ]

    def save_one(self, pic: bytes, rq: ImgRequest) -> Path:
        folder = self._dir / self._pano
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{rq.fov}-{rq.heading}.jpg"
        with open(folder / name, "wb") as f:
            f.write(pic)
        return folder / name


class PanoFolderGlued(PanoFolder):
    def __init__(self, directory: Path, pano_id: str, duplicate_on_seams: bool = True):
        self._dir: Path = directory
        self._pano: str = pano_id
        self._dup_on_seams: bool = duplicate_on_seams

    def save(self, pics: List[Tuple[bytes, ImgRequest]]) -> List[Path]:
        pics = self._seams_handled(self._sorted(pics))
        images = [Image.open(io.BytesIO(bts)) for bts, rq in pics]
        widths, heights = zip(*(i.size for i in images))
        total_width = sum(widths)
        max_height = max(heights)
        new_im = Image.new("RGB", (total_width, max_height))
        x_offset = 0
        for im in images:
            new_im.paste(im, (x_offset, 0))
            x_offset += im.size[0]
        return self._save(new_im, pics)

    @staticmethod
    def _sorted(pics: List[Tuple[bytes, ImgRequest]]) -> List[Tuple[bytes, ImgRequest]]:
        def heading(bts_rq: Tuple[bytes, ImgRequest]):
            bts, rq = bts_rq
            return rq.fov

        return sorted(pics, key=heading)

    def _seams_handled(self, pics: List[Tuple[bytes, ImgRequest]]) -> List[Tuple[bytes, ImgRequest]]:
        if self._dup_on_seams:
            return [pics[-1], *pics]
        return pics

    def _save(self, img: Image.Image, pics: List[Tuple[bytes, ImgRequest]]) -> List[Path]:
        folder = self._dir / self._pano
        folder.mkdir(parents=True, exist_ok=True)
        name = "--".join(f"{rq.fov}-{rq.heading}" for _, rq in pics) + ".jpg"
        path = folder / name
        img.save(path)
        return [path]


class Catalogue:
    def __init__(self, directory: Path, session: requests.Session, fov: int,
                 pano_folder: Callable[[Path, str], PanoFolder] = PanoFolderSimple):
        self._dir: Path = directory
        self._session: requests.Session = session
        self._fov: int = fov
        self._folder: Callable[[Path, str], PanoFolder] = pano_folder

    def add(self, panos: Panos):
        if (self._dir / "index.csv").exists():
            raise ValueError(f"index.csv already exists in {self._dir}")
        self._dir.mkdir(exist_ok=True)
        with open(self._dir / "index.csv", "w") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["pano_id", "latitude", "longitude"])
            panos_list = panos.as_list()
            logger.info(f"Got {len(panos_list)} panos to explore.")
            for i, pano in enumerate(panos_list, start=1):
                logger.info(f"Getting pano {i} of {len(panos_list)}...")
                if self.download(pano):
                    writer.writerow(
                        [
                            pano.id(),
                            pano.location().latitude,
                            pano.location().longitude,
                        ]
                    )

    def download(self, pano: Pano) -> bool:
        pano_folder = self._folder(self._dir, pano.id())
        images = []
        for rq in pano.image_requests(self._fov):
            logger.info(f"Downloading {rq.url!r} ...")
            resp = self._session.get(rq.url)
            if resp.ok:
                images.append((resp.content, rq))
            else:
                logger.warning(f"Got error downloading {rq.url!r} : {resp.status_code}.")
        pano_folder.save(images)
        return len(images) > 0


@click.command()
@click.argument('centre')
@click.argument('output_folder')
@click.option('--glue', is_flag=True, help='Glue together pics of the panoramic view.')
@click.option('--fov', default=90, help='field of view. Defaults to 90 degrees')
@click.option('--square-side', default=500, help='Side of the square to be crawler. Defaults to 500 meters')
@click.option('--step', default=10, help='Crawling step in meters. Defaults to 10')
def main(centre: str, output_folder: str, glue: bool, fov: int, square_side: int, step: int):
    api_key = os.environ["STREETVIEW_API_KEY"]
    session = requests.session()
    Catalogue(
        Path(output_folder),
        session,
        fov,
        PanoFolderSimple if not glue else PanoFolderGlued
    ).add(
        Panos(
            PointsInSquare(
                centre=parsed_centre(centre),
                square_side=square_side,
                step=step,
            ),
            pano_id=lambda coords: PanoIdOf(coords, api_key, session),
            pano=lambda pano_id, location: Pano(pano_id, location, api_key)
        )
    )


if __name__ == "__main__":
    main()
