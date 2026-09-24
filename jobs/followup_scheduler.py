from __future__ import annotations

import asyncio

from app.config import get_settings
from app.db import close_pool, get_pool
from app.followup import send_due

if __name__ == "__main__":
    from twilio.rest import Client as TwilioClient

    async def _main() -> None:
        settings = get_settings()
        pool = await get_pool()
        client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
        try:
            await send_due(pool, twilio_client=client)
        finally:
            await close_pool()

    asyncio.run(_main())
