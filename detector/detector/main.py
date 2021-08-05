import io
from abc import ABC, abstractmethod

import click
from loguru import logger
from pathlib import Path
from typing import List, NamedTuple
from paddleocr import PaddleOCR
from google.cloud import vision


class Pano:
    def __init__(self, path: Path):
        self._path: Path = path

    def pics(self) -> List[Path]:
        return [
            p for p in
            self._path.iterdir()
            if p.is_file()
        ]

    def id(self) -> str:
        return self._path.parts[-1]


class Inspection(ABC):
    @abstractmethod
    def text_of(self, p: Path) -> List[str]:
        pass


class InspectionPaddle(Inspection):
    def __init__(self, ocr: PaddleOCR):
        self._ocr: PaddleOCR = ocr

    def text_of(self, p: Path) -> List[str]:
        out = []
        txts = self._text_of(p)
        return interesting(txts)

    def _text_of(self, p: Path) -> List[str]:
        return [
            record[1][0].lower()
            for record in
            self._ocr.ocr(str(p))
        ]


class InspectionGoogle(Inspection):
    def __init__(self):
        self._client = vision.ImageAnnotatorClient()

    def text_of(self, p: Path) -> List[str]:
        txts = self._text_of(p)
        for txt in txts:
            logger.info(f"Found: {txt!r}")
        return interesting(txts)

    def _text_of(self, p: Path) -> List[str]:
        with io.open(p, "rb") as image_file:
            content = image_file.read()
        image = vision.Image(content=content)
        return [
            t.description
            for t in
            self._client.text_detection(image=image, image_context={"language_hints": ["uk"]}).text_annotations
        ]


def interesting(words: List[str]) -> List[str]:
    return [
        t for t in words
        if "евро" in t or "евро" in t or "euro" in t
    ]


class PreInspection(ABC):
    @abstractmethod
    def has_text(self, p: Path) -> bool:
        pass


class SmartInspection(Inspection):
    def __init__(self, pre_inspection: PreInspection, costly_inspection: Inspection):
        self._pre: PreInspection = pre_inspection
        self._inspection: Inspection = costly_inspection

    def text_of(self, p: Path) -> List[str]:
        if self._pre.has_text(p):
            return self._inspection.text_of(p)
        return []


class FoundItem(NamedTuple):
    text: str
    pano_id: str


class Catalogue:
    def __init__(self, path: Path):
        self._path: Path = path

    def panos(self) -> List[Pano]:
        return [
            Pano(p) for p in
            self._path.iterdir()
            if p.is_dir()
        ]

    def inspect_via(self, inspection: Inspection) -> List[FoundItem]:
        out = []
        panos = self.panos()
        for i, pano in enumerate(panos):
            logger.info(f"Looking at pano {i} of {len(panos)} ...")
            for path in pano.pics():
                for text in inspection.text_of(path):
                    logger.info(f"Got {text!r} on {pano.id()}.")
                    out.append(FoundItem(text, pano.id()))
        return out


def validate_inspection(ctx, param, value):
    valid = ("google", "paddle")
    if value not in valid:
        raise click.BadParameter(f"inspection must be one of {valid}")


@click.command()
@click.argument('catalogue_path')
@click.option('--inspection', default="google", callback=validate_inspection)
def main(catalogue_path: str, inspection: str):
    out = Catalogue(
        Path(catalogue_path),
    ).inspect_via(
        InspectionGoogle()
        if inspection == "google" else
        InspectionPaddle(
            PaddleOCR(lang="uk")
        )
    )
    for txt, pano_id in out:
        print(txt, pano_id)


if __name__ == '__main__':
    main()
