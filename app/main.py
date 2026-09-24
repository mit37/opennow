from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from twilio.twiml.messaging_response import MessagingResponse

from app.db import close_pool, get_pool
from app.router import handle_message
from app.security import validate_twilio_signature


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="OpenNow", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/sms")
async def sms_webhook(request: Request) -> Response:
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    signature = request.headers.get("X-Twilio-Signature", "")
    webhook_url = str(request.url)
    if not validate_twilio_signature(webhook_url, params, signature):
        return Response(status_code=403)

    from_number = params.get("From", "")
    body = params.get("Body", "")

    pool = await get_pool()
    reply_text = await handle_message(pool, from_number, body)

    twiml = MessagingResponse()
    twiml.message(reply_text)
    return Response(content=str(twiml), media_type="application/xml")
