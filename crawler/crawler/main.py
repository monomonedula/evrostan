import os
import sys
from pathlib import Path

import requests

from crawler.crawler.api import Catalogue, Panos, PointsInSquare, parsed_centre, PanoIdOf, Pano


def main():
    api_key = os.environ["STREETVIEW_API_KEY"]
    session = requests.session()
    Catalogue(
        Path(sys.argv[2]),
        session,
    ).add(
        Panos(
            PointsInSquare(
                centre=parsed_centre(sys.argv[1]),
                square_side=2000,
                step=10,
            ),
            pano_id=lambda coords: PanoIdOf(coords, api_key, session),
            pano=lambda pano_id, location: Pano(pano_id, location, api_key)
        )
    )


if __name__ == "__main__":
    main()
