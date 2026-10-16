from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import authors as authors_module
from .. import models
from ..db import get_db

router = APIRouter()


class AuthorStatusIn(BaseModel):
    status: Literal["watch", "candidate", "ignore"]


@router.get("/api/authors")
def list_authors(status: Literal["watch", "candidate", "ignore"] | None = None, db: Session = Depends(get_db)):
    """Watched, suggested and ignored GitHub authors with what their repositories yielded, best first."""
    return authors_module.list_authors(db, status)


@router.post("/api/authors/{author_id}/status")
def set_author_status(author_id: int, body: AuthorStatusIn, db: Session = Depends(get_db)):
    author = db.get(models.Author, author_id)
    if author is None:
        raise HTTPException(404, "Author not found")
    authors_module.set_status(db, author, body.status)
    db.commit()
    return authors_module.serialize(author)
