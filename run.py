import logging

import uvicorn
from app.config import get_settings
from app import create_app

logging.basicConfig(level=logging.INFO)
settings = get_settings()
app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "run:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
        log_level="info",
    )
