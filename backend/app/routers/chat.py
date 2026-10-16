from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import chat as chat_module
from .. import schemas
from ..db import get_db

router = APIRouter()


@router.post("/api/chat")
def chat(body: schemas.ChatIn, db: Session = Depends(get_db)):
    return chat_module.answer(db, body.message, body.history)
