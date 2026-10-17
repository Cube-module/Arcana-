import json
import logging
from pathlib import Path
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from app.config import ROOT
from app.models.orm import Card


def load_deck(path=None):
    items = json.loads((path or ROOT / "assets/deck.json").read_text(encoding="utf-8"))
    if len(items) != 78 or {x["id"] for x in items} != set(range(78)) or len({x["code"] for x in items}) != 78:
        raise ValueError("Deck must contain unique IDs 0..77 and unique codes")
    for item in items:
        if not item["name"].strip() or not item["meaning"].strip() or len(item["meaning"]) > 650:
            raise ValueError("Card name or meaning is invalid")
        path = (ROOT / item["image_path"]).resolve()
        if not path.is_relative_to((ROOT / "assets/images").resolve()):
            raise ValueError("Image must be inside assets/images")
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise ValueError("Expected PNG/JPEG")
        if path.exists() and path.stat().st_size > 1024 * 1024:
            raise ValueError("Image exceeds 1 MiB")
    return items


async def seed_deck(session):
    for item in load_deck():
        await session.execute(insert(Card).values(**item).on_conflict_do_update(index_elements=[Card.id], set_=item))


async def startup_check(sessions):
    items = load_deck()
    async with sessions() as session:
        version = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if version != "0001":
            raise ValueError("Apply migrations first")
        cards = list((await session.scalars(select(Card))).all())
        if len(cards) != 78 or any(not c.meaning.strip() for c in cards):
            raise ValueError("Load the complete deck first")
        for item in items:
            if not (ROOT / item["image_path"]).exists():
                logging.getLogger("arcana").warning("request_id=startup action=deck error_code=MISSING_IMAGE card_id=%s", item["id"])
                if not (ROOT / "assets/images/back.jpg").exists():
                    raise ValueError("Missing image and fallback back.jpg")
